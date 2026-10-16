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
 * camera sees onto a <canvas>, once per animation frame.
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

  constructor(private readonly container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    // Retina screens report 2-3 device pixels per CSS pixel; more than 2 costs a lot for
    // little visible gain.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
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

    this.dayNight = new DayNight(this.scene);
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.renderer.setAnimationLoop((time) => this.frame(time));
  }

  /**
   * Replace whatever is shown with this city. A new city also points the camera at it; moving
   * through the timeline passes keepCamera, so the view stays put while the city changes.
   */
  show(city: CityLayout, { keepCamera = false } = {}): void {
    this.clear();
    this.ground = new GroundLayer(city);
    this.buildings = new BuildingLayer(city.buildings);
    this.bridges = new BridgeLayer(city);
    this.scene.add(this.ground.group, this.buildings.group, this.bridges.group);

    const radius = Math.max(city.radius, 40);
    this.dayNight.fitCity(radius, [this.ground, this.buildings, this.bridges]);
    if (!keepCamera) {
      this.frameCamera(radius);
    }
  }

  /** The building under a point on the screen (in CSS pixels), if any. */
  buildingAt(clientX: number, clientY: number): Building | null {
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
    this.controls.update(); // Required every frame for damping.
    this.dayNight.update(delta);
    this.buildings?.update(time / 1000);
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
  }
}
