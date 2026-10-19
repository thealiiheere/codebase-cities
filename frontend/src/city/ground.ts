import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { CityLayout } from "../api";
import type { NightAware } from "./lighting";

const ASPHALT = "#22272e";
const PLOT_BLEND = new THREE.Color("#1b1f24");

/** The objects that make up one district on the ground. */
interface DistrictParts {
  language: string;
  objects: THREE.Object3D[];
  label: HTMLElement;
}

/** The ground: asphalt everywhere, the plaza in the middle, and a tinted plot per district. */
export class GroundLayer implements NightAware {
  readonly group = new THREE.Group();
  private readonly disposables: { dispose(): void }[] = [];
  private readonly labels: CSS2DObject[] = [];
  private readonly plazaGlow: THREE.MeshStandardMaterial;
  private readonly borders: THREE.LineBasicMaterial[] = [];
  private readonly districts: DistrictParts[] = [];

  constructor(city: CityLayout) {
    // Far bigger than the city, so its edge always lies beyond the fog and never shows.
    const extent = Math.max(city.radius, 60) * 16;
    const asphalt = this.track(new THREE.PlaneGeometry(extent, extent));
    asphalt.rotateX(-Math.PI / 2); // Planes stand upright by default; lay this one flat.
    const ground = new THREE.Mesh(
      asphalt,
      this.track(new THREE.MeshStandardMaterial({ color: ASPHALT, roughness: 1 })),
    );
    ground.receiveShadow = true;
    this.group.add(ground);

    // The plaza: a light stone square with a glowing fountain in the middle.
    const size = city.plaza.size;
    const plaza = new THREE.Mesh(
      this.track(new THREE.BoxGeometry(size, 0.3, size)),
      this.track(new THREE.MeshStandardMaterial({ color: "#9ea7b3", roughness: 0.9 })),
    );
    plaza.position.y = 0.15;
    plaza.receiveShadow = true;
    this.plazaGlow = this.track(
      new THREE.MeshStandardMaterial({ color: "#7dd3fc", emissive: "#38bdf8", roughness: 0.3 }),
    );
    const fountain = new THREE.Mesh(this.track(new THREE.CylinderGeometry(size * 0.15, size * 0.18, 0.8, 24)), this.plazaGlow);
    fountain.position.y = 0.7;
    this.group.add(plaza, fountain);

    for (const district of city.districts) {
      this.addDistrict(district);
    }
  }

  setNight(amount: number): void {
    this.plazaGlow.emissiveIntensity = THREE.MathUtils.lerp(0.1, 1.4, amount);
    for (const border of this.borders) {
      border.opacity = THREE.MathUtils.lerp(0.35, 0.9, amount);
    }
  }

  /**
   * Show each district with its number of standing buildings, hiding districts with none: on
   * the timeline, a district appears with its first building.
   */
  setCounts(counts: Map<string, { standing: number }>): void {
    for (const { language, objects, label } of this.districts) {
      const count = counts.get(language)?.standing ?? 0;
      // An invisible label is hidden on the page too (CSS2DRenderer sets display: none).
      for (const object of objects) object.visible = count > 0;
      label.textContent = `${language} · ${count}`;
    }
  }

  dispose(): void {
    // Removing a label from its parent also removes its HTML element from the page.
    for (const label of this.labels) label.removeFromParent();
    for (const item of this.disposables) item.dispose();
  }

  private addDistrict(district: CityLayout["districts"][number]): void {
    // A low slab in a muted version of the language color, so buildings stay the brightest.
    const plot = new THREE.Mesh(
      this.track(new THREE.BoxGeometry(district.width, 0.2, district.depth)),
      this.track(
        new THREE.MeshStandardMaterial({
          color: new THREE.Color(district.color).lerp(PLOT_BLEND, 0.72),
          roughness: 1,
        }),
      ),
    );
    plot.position.set(district.x, 0.1, district.z);
    plot.receiveShadow = true;

    // A colored outline around the plot.
    const halfW = district.width / 2;
    const halfD = district.depth / 2;
    const corners = [
      new THREE.Vector3(-halfW, 0, -halfD),
      new THREE.Vector3(halfW, 0, -halfD),
      new THREE.Vector3(halfW, 0, halfD),
      new THREE.Vector3(-halfW, 0, halfD),
    ];
    const material = this.track(
      new THREE.LineBasicMaterial({ color: district.color, transparent: true, opacity: 0.9 }),
    );
    this.borders.push(material);
    const outline = new THREE.LineLoop(this.track(new THREE.BufferGeometry().setFromPoints(corners)), material);
    outline.position.set(district.x, 0.25, district.z);

    // The district's name, as an HTML label that Three.js keeps positioned over the 3D point.
    const element = document.createElement("div");
    element.className = "district-label";
    element.textContent = `${district.language} · ${district.repo_count}`;
    // Lighten the color a bit so dark languages (like Lua's navy) stay readable.
    element.style.color = new THREE.Color(district.color).lerp(new THREE.Color("#ffffff"), 0.35).getStyle();
    const label = new CSS2DObject(element);
    label.position.set(district.x, 0.5, district.z - halfD);
    this.labels.push(label);

    this.group.add(plot, outline, label);
    this.districts.push({ language: district.language, objects: [plot, outline, label], label: element });
  }

  private track<T extends { dispose(): void }>(item: T): T {
    this.disposables.push(item);
    return item;
  }
}
