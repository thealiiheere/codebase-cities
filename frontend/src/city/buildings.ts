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

/**
 * Every building is a stack of floors, and every floor is one box. Drawing thousands of boxes
 * one by one would be slow, so all floors share one geometry inside an InstancedMesh: the GPU
 * draws them in a single call, each with its own position, size, and color.
 *
 * Floors with lit windows and floors without are two separate InstancedMeshes, because only
 * the first kind gets a glowing ("emissive") window texture at night.
 */
export class BuildingLayer implements NightAware {
  readonly group = new THREE.Group();
  /** For each pickable mesh: which building every one of its instances belongs to. */
  private readonly owners = new Map<THREE.Object3D, number[]>();
  private readonly textures = floorTextures();
  private readonly geometries: THREE.BufferGeometry[] = [];
  private readonly litWalls: THREE.MeshStandardMaterial;
  private readonly darkWalls: THREE.MeshStandardMaterial;
  private readonly roofs: THREE.MeshStandardMaterial;
  private readonly fixtures: THREE.MeshStandardMaterial;
  private readonly lights: THREE.MeshStandardMaterial;
  private night = 1;

  constructor(private readonly buildings: Building[]) {
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
    this.fixtures = new THREE.MeshStandardMaterial({ color: "#8b949e", metalness: 0.6, roughness: 0.4 });
    this.lights = new THREE.MeshStandardMaterial({ color: "#fff6d5", emissive: "#ffd36b" });

    this.addFloors();
    this.addRoofFixtures();
  }

  /** Meshes the mouse can hover. */
  get pickables(): THREE.Object3D[] {
    return [...this.owners.keys()];
  }

  /** The building a raycast hit, if it hit one of our floors. */
  pick(hit: THREE.Intersection): Building | null {
    const owners = this.owners.get(hit.object);
    if (owners === undefined || hit.instanceId === undefined) {
      return null;
    }
    return this.buildings[owners[hit.instanceId]] ?? null;
  }

  setNight(amount: number): void {
    this.night = amount;
    this.litWalls.emissiveIntensity = 1.1 * amount;
  }

  /** Per-frame animation: rooftop lights pulse gently at night. */
  update(seconds: number): void {
    const pulse = 0.8 + 0.2 * Math.sin(seconds * 2.2);
    this.lights.emissiveIntensity = THREE.MathUtils.lerp(0.4, 3.2 * pulse, this.night);
  }

  dispose(): void {
    for (const geometry of this.geometries) geometry.dispose();
    for (const material of [this.litWalls, this.darkWalls, this.roofs, this.fixtures, this.lights]) {
      material.dispose();
    }
    this.textures.wall.dispose();
    this.textures.glow.dispose();
  }

  private addFloors(): void {
    const lit: Placement[] = [];
    const dark: Placement[] = [];
    this.buildings.forEach((building, index) => {
      const floorHeight = building.height / building.floors;
      for (let floor = 0; floor < building.floors; floor++) {
        const width = building.size * widthScale(building, floor);
        const placement: Placement = {
          owner: index,
          color: building.color,
          matrix: boxMatrix(building.x, floorHeight * (floor + 0.5), building.z, width, floorHeight, width),
        };
        // window_brightness is the share of floors that are lit. hash01 picks which ones, the
        // same way every time.
        const isLit = hash01(`${building.repo}:${floor}`) < building.window_brightness;
        (isLit ? lit : dark).push(placement);
      }
    });

    // A box has six faces, in this order: +x, -x, top, bottom, +z, -z. Sides get the window
    // texture; top and bottom get the plain roof material.
    const box = this.geometry(new THREE.BoxGeometry(1, 1, 1));
    const faces = (walls: THREE.Material) => [walls, walls, this.roofs, this.roofs, walls, walls];
    this.addInstances(box, faces(this.litWalls), lit, true);
    this.addInstances(box, faces(this.darkWalls), dark, true);
  }

  private addRoofFixtures(): void {
    const antennas: Placement[] = [];
    const boxes: Placement[] = [];
    const lights: Placement[] = [];

    this.buildings.forEach((building, index) => {
      const top = building.height;
      const roofWidth = building.size * widthScale(building, building.floors - 1);
      if (building.style === STYLE_ANTENNA) {
        const height = 4 + building.floors * 0.25;
        antennas.push({ owner: index, color: "#ffffff", matrix: boxMatrix(building.x, top + height / 2, building.z, 1, height, 1) });
      } else if (building.style === STYLE_ROOF_BOX && roofWidth > 4) {
        const side = roofWidth * 0.35;
        // Off-center by a stable amount, so not every rooftop looks the same.
        const shift = (hash01(building.repo) - 0.5) * roofWidth * 0.3;
        boxes.push({ owner: index, color: "#ffffff", matrix: boxMatrix(building.x + shift, top + 1.2, building.z - shift, side, 2.4, side) });
      }
      // Rooftop lights (one per star "level") in a ring near the roof's edge.
      const ring = roofWidth * 0.38;
      const turn = hash01(`${building.repo}:lights`) * Math.PI * 2;
      for (let k = 0; k < building.roof_lights; k++) {
        const angle = turn + (k / building.roof_lights) * Math.PI * 2;
        const x = building.x + Math.cos(angle) * ring;
        const z = building.z + Math.sin(angle) * ring;
        lights.push({ owner: index, color: "#ffffff", matrix: boxMatrix(x, top + 0.35, z, 1, 1, 1) });
      }
    });

    this.addInstances(this.geometry(new THREE.CylinderGeometry(0.12, 0.25, 1, 6)), this.fixtures, antennas, false);
    this.addInstances(this.geometry(new THREE.BoxGeometry(1, 1, 1)), this.fixtures, boxes, false);
    this.addInstances(this.geometry(new THREE.SphereGeometry(0.35, 8, 6)), this.lights, lights, false);
  }

  private addInstances(
    geometry: THREE.BufferGeometry,
    material: THREE.Material | THREE.Material[],
    placements: Placement[],
    pickable: boolean,
  ): void {
    if (placements.length === 0) {
      return;
    }
    const mesh = new THREE.InstancedMesh(geometry, material, placements.length);
    const color = new THREE.Color();
    placements.forEach((placement, i) => {
      mesh.setMatrixAt(i, placement.matrix);
      mesh.setColorAt(i, color.set(placement.color));
    });
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    // Needed for correct culling and mouse picking once instances are spread across the city.
    mesh.computeBoundingSphere();
    mesh.computeBoundingBox();
    this.group.add(mesh);
    if (pickable) {
      this.owners.set(mesh, placements.map((placement) => placement.owner));
    }
  }

  private geometry<T extends THREE.BufferGeometry>(geometry: T): T {
    this.geometries.push(geometry);
    return geometry;
  }
}

interface Placement {
  owner: number;
  color: string;
  matrix: THREE.Matrix4;
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
