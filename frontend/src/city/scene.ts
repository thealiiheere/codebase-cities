import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { Building, CityLayout } from "../api";
import { BridgeLayer } from "./bridges";
import { BuildingLayer } from "./buildings";
import { GroundLayer } from "./ground";
import { DayNight } from "./lighting";

/**
 * The 3D view: owns the renderer, camera, controls, and lights, and draws one city at a time.
 *
 * Three.js basics: a Scene holds objects; a Camera looks at it; a Renderer draws what the
 * camera sees onto a <canvas>.
 *
 * It only draws when something changed (the camera moved, the light is changing, the city or
 * the timeline changed). A city standing still costs nothing, which keeps laptops cool and the
 * page responsive.
 */
export class CityView {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly labelRenderer = new CSS2DRenderer();
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(50, 1, 0.5, 5000);
  private readonly controls: OrbitControls;
  private readonly dayNight: DayNight;
  private readonly raycaster = new THREE.Raycaster();
  private ground?: GroundLayer;
  private buildings?: BuildingLayer;
  private bridges?: BridgeLayer;
  private lastFrame = 0;
  private needsRender = true;
  /** Frames actually drawn, for checking the render-on-demand behavior while developing. */
  renderCount = 0;

  constructor(private readonly container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    // High-DPI screens report 1.5-3 device pixels per CSS pixel. Each step up multiplies the
    // pixels to draw, and above 1.5 the gain is hard to see, so cap it there.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    this.renderer.shadowMap.enabled = true;
    // Percentage-closer filtering smooths shadow edges. (Three.js removed the separate "soft"
    // variant; this is its replacement.)
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    // Filmic tone mapping keeps bright window glow from blowing out to flat white.
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    container.appendChild(this.renderer.domElement);

    // District labels are HTML elements layered over the canvas, kept in place by this renderer.
    Object.assign(this.labelRenderer.domElement.style, {
      position: "absolute",
      inset: "0",
      pointerEvents: "none",
    });
    container.appendChild(this.labelRenderer.domElement);

    // OrbitControls: drag to rotate around a target point, right-drag to pan, scroll to zoom.
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; // Glide to a stop instead of halting instantly.
    this.controls.maxPolarAngle = Math.PI * 0.47; // Never tilt below the ground.
    this.controls.screenSpacePanning = false; // Pan along the ground, not up into the sky.
    this.controls.addEventListener("change", () => this.requestRender());

    this.dayNight = new DayNight(this.scene);
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.renderer.setAnimationLoop((time) => this.frame(time));
  }

  /** Replace whatever is shown with this city (as it is today), and point the camera at it. */
  show(city: CityLayout): void {
    this.clear();
    this.ground = new GroundLayer(city);
    this.buildings = new BuildingLayer(city.buildings);
    this.bridges = new BridgeLayer(city);
    this.scene.add(this.ground.group, this.buildings.group, this.bridges.group);

    const radius = Math.max(city.radius, 40);
    this.dayNight.fitCity(radius, [this.ground, this.buildings, this.bridges]);
    this.frameCamera(radius);
    this.requestRender();
  }

  /** Ask for one new frame; the next animation frame draws it. */
  requestRender(): void {
    this.needsRender = true;
  }

  /**
   * Show the city as it was at the end of `date` (YYYY-MM-DD), or today's city for null:
   * buildings have the floors they had then, and districts and bridges appear with them.
   * Returns how many buildings stand at that date.
   */
  setTime(date: string | null): number {
    const buildings = this.buildings;
    if (buildings === undefined) {
      return 0;
    }
    if (buildings.setTime(date)) {
      this.ground?.setCounts(buildings.districtCounts());
      this.bridges?.showWhen((repo) => buildings.isComplete(repo));
      this.requestRender();
    }
    return buildings.standing();
  }

  /** The building under a point on the screen (in CSS pixels), and its floors at the date shown. */
  buildingAt(clientX: number, clientY: number): { building: Building; floors: number } | null {
    if (this.buildings === undefined) {
      return null;
    }
    // Convert the pointer to "normalized device coordinates": -1..1 across the canvas.
    const bounds = this.renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((clientX - bounds.left) / bounds.width) * 2 - 1,
      -((clientY - bounds.top) / bounds.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(pointer, this.camera);
    const [hit] = this.raycaster.intersectObjects(this.buildings.pickables, false);
    return hit ? this.buildings.pick(hit) : null;
  }

  /** Call `listener` whenever the camera moves (including while it glides to a stop). */
  onCameraMove(listener: () => void): void {
    this.controls.addEventListener("change", listener);
  }

  /** Switch between day and night. Returns true when it is now (becoming) night. */
  toggleDayNight(): boolean {
    this.dayNight.toggle();
    this.requestRender();
    return this.dayNight.isNight;
  }

  get canvas(): HTMLCanvasElement {
    return this.renderer.domElement;
  }

  private clear(): void {
    for (const layer of [this.ground, this.buildings, this.bridges]) {
      if (layer) {
        this.scene.remove(layer.group);
        layer.dispose(); // Free GPU memory held by geometries, materials, and textures.
      }
    }
    this.ground = this.buildings = this.bridges = undefined;
  }

  private frameCamera(radius: number): void {
    const distance = radius * 1.5;
    this.camera.position.set(distance * 0.75, distance * 0.65, distance * 0.75);
    this.camera.far = radius * 14;
    this.camera.updateProjectionMatrix();
    this.controls.target.set(0, 0, 0);
    this.controls.minDistance = 12;
    this.controls.maxDistance = radius * 4;
    this.controls.update();
  }

  private frame(time: number): void {
    const delta = this.lastFrame ? (time - this.lastFrame) / 1000 : 0;
    this.lastFrame = time;
    // Both are cheap; they ask for a render (via the controls' "change" event, or by returning
    // true) only while the camera glides or the light is changing.
    this.controls.update();
    if (this.dayNight.update(delta)) {
      this.needsRender = true;
    }
    if (!this.needsRender) {
      return;
    }
    this.needsRender = false;
    this.renderCount++;
    this.renderer.render(this.scene, this.camera);
    this.labelRenderer.render(this.scene, this.camera);
  }

  private resize(): void {
    const { clientWidth: width, clientHeight: height } = this.container;
    if (width === 0 || height === 0) {
      return;
    }
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
    this.labelRenderer.setSize(width, height);
    this.requestRender();
  }
}
