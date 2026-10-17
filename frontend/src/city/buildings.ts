import * as THREE from "three";
import type { Building } from "../api";
import { hash01 } from "./hash";
import type { NightAware } from "./lighting";

// Building styles from the backend (Building.style).
const STYLE_SETBACK = 1; // The top floors are narrower.
const STYLE_ANTENNA = 2;
const STYLE_ROOF_BOX = 3; // A small box on the roof, like a water tank or elevator room.

const SETBACK_FROM = 0.75; // Floors above this share of the height get narrower...
const SETBACK_SCALE = 0.72; // ...by this much.
const WINDOW_GLOW = new THREE.Color("#ffcf7a");

// An instance scaled to nothing: how a floor that doesn't exist (yet) is "removed" without
// rebuilding the mesh.
const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0);

/** What one building looks like at the moment the timeline shows. */
interface BuildingState {
  floors: number;
  brightness: number;
}

/** Something on a roof (antenna, rooftop box, light) that rides up as the building grows. */
interface Fixture {
  owner: number;
  mesh: THREE.InstancedMesh;
  index: number;
  x: number;
  z: number;
  size: THREE.Vector3;
  /** Height of its center above the roof. */
  above: number;
}

/**
 * Every building is a stack of floors, and every floor is one box. Drawing thousands of boxes
 * one by one would be slow, so all floors share one geometry inside an InstancedMesh: the GPU
 * draws them in a single call, each with its own position, size, and color.
 *
 * Floors with lit windows and floors without are two InstancedMeshes, because only the first
 * kind gets a glowing ("emissive") window texture at night. Every floor of the finished city has
 * a slot in both; the timeline shows each floor in one of them, or in neither when it hasn't
 * been built yet. Changing the date only rewrites the slots of buildings that changed.
 */
export class BuildingLayer implements NightAware {
  readonly group = new THREE.Group();
  private readonly textures = floorTextures();
  private readonly geometries: THREE.BufferGeometry[] = [];
  private readonly litWalls: THREE.MeshStandardMaterial;
  private readonly darkWalls: THREE.MeshStandardMaterial;
  private readonly roofs: THREE.MeshStandardMaterial;
  private readonly fixtureMaterial: THREE.MeshStandardMaterial;
  private readonly lightMaterial: THREE.MeshStandardMaterial;
  private readonly litFloors: THREE.InstancedMesh;
  private readonly darkFloors: THREE.InstancedMesh;
  /** Index of each building's first floor slot (its floors follow in order). */
  private readonly firstSlot: number[] = [];
  /** Building index for every floor slot, for mouse picking. */
  private readonly slotOwner: number[] = [];
  private readonly floorMatrices: THREE.Matrix4[] = [];
  /** Per floor slot, the 0..1 value its brightness must exceed for its windows to be lit. */
  private readonly litThreshold: number[] = [];
  /** Roof fixtures per building. */
  private readonly fixtures: Fixture[][];
  private readonly fixtureMeshes: THREE.InstancedMesh[] = [];
  private readonly indexOf: Map<string, number>;
  private readonly state: BuildingState[];

  constructor(private readonly buildings: Building[]) {
    this.fixtures = buildings.map(() => []);
    this.indexOf = new Map(buildings.map((building, index) => [building.repo, index]));
    // Instance colors multiply the material color, so materials are white (or gray) and each
    // floor is tinted with its building's language color.
    this.darkWalls = new THREE.MeshStandardMaterial({
      map: this.textures.wall,
      roughness: 0.85,
      metalness: 0.05,
    });
    this.litWalls = this.darkWalls.clone();
    this.litWalls.emissive = WINDOW_GLOW;
    this.litWalls.emissiveMap = this.textures.glow;
    this.roofs = new THREE.MeshStandardMaterial({ color: "#9aa1a9", roughness: 0.95 });
    this.fixtureMaterial = new THREE.MeshStandardMaterial({ color: "#8b949e", metalness: 0.6, roughness: 0.4 });
    this.lightMaterial = new THREE.MeshStandardMaterial({ color: "#fff6d5", emissive: "#ffd36b" });

    this.planFloors();
    // A box has six faces, in this order: +x, -x, top, bottom, +z, -z. Sides get the window
    // texture; top and bottom get the plain roof material.
    const box = this.geometry(new THREE.BoxGeometry(1, 1, 1));
    const faces = (walls: THREE.Material) => [walls, walls, this.roofs, this.roofs, walls, walls];
    this.litFloors = this.floorMesh(box, faces(this.litWalls));
    this.darkFloors = this.floorMesh(box, faces(this.darkWalls));
    this.addRoofFixtures();

    // Start with the finished city (today).
    this.state = buildings.map((building) => ({
      floors: building.floors,
      brightness: building.window_brightness,
    }));
    buildings.forEach((_, index) => this.applyState(index));
  }

  /** Meshes the mouse can hover. */
  get pickables(): THREE.Object3D[] {
    return [this.litFloors, this.darkFloors];
  }

  /** The building a raycast hit (and its floors at the date shown), if it hit one of ours. */
  pick(hit: THREE.Intersection): { building: Building; floors: number } | null {
    if (hit.instanceId === undefined || !this.pickables.includes(hit.object)) {
      return null;
    }
    const index = this.slotOwner[hit.instanceId];
    return index === undefined ? null : { building: this.buildings[index], floors: this.state[index].floors };
  }

  /**
   * Show the city as it was at the end of `date` (YYYY-MM-DD), or as it is today for null.
   * Returns true if anything changed (so the view knows to redraw).
   */
  setTime(date: string | null): boolean {
    let changed = false;
    this.buildings.forEach((building, index) => {
      const next =
        date === null
          ? { floors: building.floors, brightness: building.window_brightness }
          : { floors: stepAt(building.growth, date, 0), brightness: stepAt(building.lights, date, 0) };
      const current = this.state[index];
      if (current.floors !== next.floors || current.brightness !== next.brightness) {
        this.state[index] = next;
        this.applyState(index);
        changed = true;
      }
    });
    if (changed) {
      // Tell Three.js to send the edited slots to the GPU before the next frame.
      for (const mesh of [this.litFloors, this.darkFloors, ...this.fixtureMeshes]) {
        mesh.instanceMatrix.needsUpdate = true;
      }
    }
    return changed;
  }

  /** Whether a repo's building stands at its full height at the date shown. */
  isComplete(repo: string): boolean {
    const index = this.indexOf.get(repo);
    return index !== undefined && this.state[index].floors === this.buildings[index].floors;
  }

  /** How many buildings stand (have at least one floor) at the date shown. */
  standing(): number {
    return this.state.filter((state) => state.floors > 0).length;
  }

  /** Buildings standing at the current date, per district. */
  districtCounts(): Map<string, number> {
    const counts = new Map<string, number>();
    this.buildings.forEach((building, index) => {
      if (this.state[index].floors > 0) {
        counts.set(building.district, (counts.get(building.district) ?? 0) + 1);
      }
    });
    return counts;
  }

  setNight(amount: number): void {
    this.litWalls.emissiveIntensity = 1.1 * amount;
    // Steady rather than pulsing: an animation would force a redraw every frame forever.
    this.lightMaterial.emissiveIntensity = THREE.MathUtils.lerp(0.4, 2.8, amount);
  }

  dispose(): void {
    for (const geometry of this.geometries) geometry.dispose();
    for (const material of [this.litWalls, this.darkWalls, this.roofs, this.fixtureMaterial, this.lightMaterial]) {
      material.dispose();
    }
    this.textures.wall.dispose();
    this.textures.glow.dispose();
  }

  /** Work out where every floor of the finished city goes. */
  private planFloors(): void {
    this.buildings.forEach((building, index) => {
      this.firstSlot.push(this.floorMatrices.length);
      const floorHeight = building.height / building.floors;
      for (let floor = 0; floor < building.floors; floor++) {
        const width = building.size * widthScale(building, floor);
        this.floorMatrices.push(boxMatrix(building.x, floorHeight * (floor + 0.5), building.z, width, floorHeight, width));
        this.slotOwner.push(index);
        // window_brightness is the share of floors that are lit. hash01 picks which ones, the
        // same way every time.
        this.litThreshold.push(hash01(`${building.repo}:${floor}`));
      }
    });
  }

  private floorMesh(geometry: THREE.BufferGeometry, materials: THREE.Material[]): THREE.InstancedMesh {
    const mesh = new THREE.InstancedMesh(geometry, materials, Math.max(this.floorMatrices.length, 1));
    const color = new THREE.Color();
    this.floorMatrices.forEach((matrix, slot) => {
      // Bounds are computed from the finished city, so picking and culling cover every floor.
      mesh.setMatrixAt(slot, matrix);
      mesh.setColorAt(slot, color.set(this.buildings[this.slotOwner[slot]].color));
    });
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.computeBoundingSphere();
    mesh.computeBoundingBox();
    this.group.add(mesh);
    return mesh;
  }

  private addRoofFixtures(): void {
    const antennas: Omit<Fixture, "mesh" | "index">[] = [];
    const boxes: Omit<Fixture, "mesh" | "index">[] = [];
    const lights: Omit<Fixture, "mesh" | "index">[] = [];

    this.buildings.forEach((building, owner) => {
      const roofWidth = building.size * widthScale(building, building.floors - 1);
      if (building.style === STYLE_ANTENNA) {
        const height = 4 + building.floors * 0.25;
        antennas.push({ owner, x: building.x, z: building.z, size: new THREE.Vector3(1, height, 1), above: height / 2 });
      } else if (building.style === STYLE_ROOF_BOX && roofWidth > 4) {
        const side = roofWidth * 0.35;
        // Off-center by a stable amount, so not every rooftop looks the same.
        const shift = (hash01(building.repo) - 0.5) * roofWidth * 0.3;
        boxes.push({ owner, x: building.x + shift, z: building.z - shift, size: new THREE.Vector3(side, 2.4, side), above: 1.2 });
      }
      // Rooftop lights (one per star "level") in a ring near the roof's edge.
      const ring = roofWidth * 0.38;
      const turn = hash01(`${building.repo}:lights`) * Math.PI * 2;
      for (let k = 0; k < building.roof_lights; k++) {
        const angle = turn + (k / building.roof_lights) * Math.PI * 2;
        lights.push({
          owner,
          x: building.x + Math.cos(angle) * ring,
          z: building.z + Math.sin(angle) * ring,
          size: new THREE.Vector3(1, 1, 1),
          above: 0.35,
        });
      }
    });

    this.addFixtures(new THREE.CylinderGeometry(0.12, 0.25, 1, 6), this.fixtureMaterial, antennas);
    this.addFixtures(new THREE.BoxGeometry(1, 1, 1), this.fixtureMaterial, boxes);
    this.addFixtures(new THREE.SphereGeometry(0.35, 8, 6), this.lightMaterial, lights);
  }

  private addFixtures(
    geometry: THREE.BufferGeometry,
    material: THREE.Material,
    specs: Omit<Fixture, "mesh" | "index">[],
  ): void {
    if (specs.length === 0) {
      geometry.dispose();
      return;
    }
    const mesh = new THREE.InstancedMesh(this.geometry(geometry), material, specs.length);
    specs.forEach((spec, index) => this.fixtures[spec.owner].push({ ...spec, mesh, index }));
    mesh.castShadow = true;
    // Three.js skips drawing a mesh whose bounds are off screen, but fixtures move up and down
    // with the timeline, so bounds computed once would go stale. There are few of them, so
    // simply always draw them.
    mesh.frustumCulled = false;
    this.fixtureMeshes.push(mesh);
    this.group.add(mesh);
  }

  /** Write one building's floor and roof slots for its current state. */
  private applyState(index: number): void {
    const building = this.buildings[index];
    const { floors, brightness } = this.state[index];
    for (let floor = 0; floor < building.floors; floor++) {
      const slot = this.firstSlot[index] + floor;
      const built = floor < floors;
      const lit = built && this.litThreshold[slot] < brightness;
      this.litFloors.setMatrixAt(slot, lit ? this.floorMatrices[slot] : HIDDEN);
      this.darkFloors.setMatrixAt(slot, built && !lit ? this.floorMatrices[slot] : HIDDEN);
    }
    // Roof fixtures sit on the current top floor (and vanish with the building).
    const top = floors * (building.height / building.floors);
    for (const { mesh, index: slot, x, z, size, above } of this.fixtures[index]) {
      mesh.setMatrixAt(slot, floors > 0 ? boxMatrix(x, top + above, z, size.x, size.y, size.z) : HIDDEN);
    }
  }

  private geometry<T extends THREE.BufferGeometry>(geometry: T): T {
    this.geometries.push(geometry);
    return geometry;
  }
}

/** The value of the last step on or before `date` (steps are [date, value], oldest first). */
function stepAt(steps: [string, number][], date: string, before: number): number {
  let value = before;
  for (const [when, stepValue] of steps) {
    // ISO dates (YYYY-MM-DD) sort correctly as plain strings.
    if (when > date) break;
    value = stepValue;
  }
  return value;
}

/** Footprint scale for one floor: 1, or narrower near the top for setback buildings. */
function widthScale(building: Building, floor: number): number {
  const setback = building.style === STYLE_SETBACK && building.floors >= 4;
  return setback && floor >= Math.ceil(building.floors * SETBACK_FROM) ? SETBACK_SCALE : 1;
}

const NO_ROTATION = new THREE.Quaternion();

/** Position and size for a unit-sized shape centered at (x, y, z). */
function boxMatrix(x: number, y: number, z: number, width: number, height: number, depth: number): THREE.Matrix4 {
  return new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), NO_ROTATION, new THREE.Vector3(width, height, depth));
}

/**
 * Two textures for one side of one floor, drawn on a canvas instead of loaded from image files:
 * `wall` is the surface (white so the building color shows, a darker slab line at the bottom,
 * dark glass windows); `glow` is black except the windows, which light up at night.
 */
function floorTextures(): { wall: THREE.CanvasTexture; glow: THREE.CanvasTexture } {
  const draw = (paint: (context: CanvasRenderingContext2D) => void) => {
    const canvas = document.createElement("canvas");
    canvas.width = 128;
    canvas.height = 64;
    const context = canvas.getContext("2d");
    if (context === null) {
      throw new Error("This browser can't draw on a canvas.");
    }
    paint(context);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  };
  const windows = (context: CanvasRenderingContext2D) => {
    for (let i = 0; i < 4; i++) {
      context.fillRect(11 + i * 30, 14, 18, 32);
    }
  };

  const wall = draw((context) => {
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, 128, 64);
    context.fillStyle = "#5a616b"; // The floor slab between stories.
    context.fillRect(0, 56, 128, 8);
    context.fillStyle = "#26303d"; // Glass.
    windows(context);
  });
  const glow = draw((context) => {
    context.fillStyle = "#000000";
    context.fillRect(0, 0, 128, 64);
    context.fillStyle = "#ffffff";
    windows(context);
  });
  return { wall, glow };
}
