import * as THREE from "three";
import type { Building, CityLayout } from "../api";
import type { NightAware } from "./lighting";

/** Glowing arcs from rooftop to rooftop, linking the most-forked repos of nearby districts. */
export class BridgeLayer implements NightAware {
  readonly group = new THREE.Group();
  private readonly material = new THREE.MeshStandardMaterial({
    color: "#7dd3fc",
    emissive: "#38bdf8",
    roughness: 0.4,
  });
  private readonly geometries: THREE.BufferGeometry[] = [];

  constructor(city: CityLayout) {
    const byRepo = new Map(city.buildings.map((building) => [building.repo, building]));
    for (const bridge of city.bridges) {
      const from = byRepo.get(bridge.from_repo);
      const to = byRepo.get(bridge.to_repo);
      if (from && to) {
        this.addArc(from, to, bridge.strength);
      }
    }
  }

  setNight(amount: number): void {
    this.material.emissiveIntensity = THREE.MathUtils.lerp(0.15, 1.8, amount);
  }

  dispose(): void {
    for (const geometry of this.geometries) geometry.dispose();
    this.material.dispose();
  }

  private addArc(from: Building, to: Building, strength: number): void {
    const start = new THREE.Vector3(from.x, from.height, from.z);
    const end = new THREE.Vector3(to.x, to.height, to.z);
    const span = start.distanceTo(end);
    // The curve is pulled toward a control point above the middle, so it rises over the
    // taller roof and arcs higher the farther apart the two buildings are.
    const control = start.clone().lerp(end, 0.5);
    control.y = Math.max(from.height, to.height) + span * 0.25 + 4;
    const curve = new THREE.QuadraticBezierCurve3(start, control, end);

    // Kept thin: in a small city the camera is close, and fat tubes would hide the buildings.
    const thickness = 0.1 + strength * 0.25;
    const geometry = new THREE.TubeGeometry(curve, 64, thickness, 8, false);
    this.geometries.push(geometry);
    this.group.add(new THREE.Mesh(geometry, this.material));
  }
}
