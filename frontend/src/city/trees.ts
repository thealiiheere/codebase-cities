import * as THREE from "three";
import type { CityLayout } from "../api";
import { hash01 } from "./hash";

// Where trees go (meters).
const SIDEWALK_INSET = 1.2; // Sidewalk trees stand this far inside a district's edge...
const SIDEWALK_SPACING = 7; // ...about this far apart.
const GROVE_SPACING = 6; // Trees in open space sit on a grid this fine...
const GROVE_SHARE = 0.45; // ...and about this share of the free grid points gets one.
const BUILDING_CLEARANCE = 2; // Never closer than this to any building (past, present, or future).
const PLAZA_TREES = 12;

const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0);
const NO_ROTATION = new THREE.Quaternion();
const GREENS = ["#2f6b3a", "#3b7d3f", "#4a8a3c", "#2d5e3a", "#5a9445"].map((hex) => new THREE.Color(hex));

interface Tree {
  /** The district it belongs to (it appears with the district), or null on the plaza. */
  district: string | null;
  x: number;
  z: number;
  /** Ground height under the tree (the plaza and district plots are raised slightly). */
  ground: number;
  scale: number;
  color: THREE.Color;
}

/**
 * Trees along every district's sidewalk, in groves wherever a district has open space, and in a
 * ring on the plaza. Positions come from hash01, so a city always gets the same trees.
 *
 * Trees avoid every building of today's city, so as the timeline plays and buildings appear,
 * they never grow into a tree. All trunks are one InstancedMesh and all crowns another.
 */
export class TreeLayer {
  readonly group = new THREE.Group();
  private readonly trees: Tree[];
  private readonly trunks: THREE.InstancedMesh;
  private readonly crowns: THREE.InstancedMesh;
  private readonly disposables: { dispose(): void }[] = [];

  constructor(city: CityLayout) {
    this.trees = [...plazaTrees(city), ...city.districts.flatMap((district) => districtTrees(city, district))];

    const trunkGeometry = this.track(new THREE.CylinderGeometry(0.18, 0.28, 1, 6));
    trunkGeometry.translate(0, 0.5, 0); // Stand on the ground instead of being centered on it.
    // A low-poly ball reads as a tree crown from a distance and is cheap to draw.
    const crownGeometry = this.track(new THREE.IcosahedronGeometry(1, 0));
    this.trunks = this.instanced(trunkGeometry, new THREE.MeshStandardMaterial({ color: "#5b4636", roughness: 1 }));
    this.crowns = this.instanced(crownGeometry, new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true }));
    this.trees.forEach((tree, index) => this.crowns.setColorAt(index, tree.color));
    this.showDistricts(null);
  }

  /** Show only the trees of these districts (and the plaza's); null shows all of them. */
  showDistricts(visible: Set<string> | null): void {
    this.trees.forEach((tree, index) => {
      const shown = tree.district === null || visible === null || visible.has(tree.district);
      const { x, z, ground, scale } = tree;
      this.trunks.setMatrixAt(index, shown ? matrix(x, ground, z, scale, 1.8 * scale) : HIDDEN);
      this.crowns.setMatrixAt(index, shown ? matrix(x, ground + 2.6 * scale, z, 1.5 * scale, 1.7 * scale) : HIDDEN);
    });
    this.trunks.instanceMatrix.needsUpdate = true;
    this.crowns.instanceMatrix.needsUpdate = true;
  }

  dispose(): void {
    for (const item of this.disposables) item.dispose();
  }

  private instanced(geometry: THREE.BufferGeometry, material: THREE.Material): THREE.InstancedMesh {
    this.track(material);
    const mesh = new THREE.InstancedMesh(geometry, material, Math.max(this.trees.length, 1));
    mesh.count = this.trees.length;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    // Trees appear and disappear with their districts, so bounds computed once could be wrong.
    mesh.frustumCulled = false;
    this.group.add(mesh);
    return mesh;
  }

  private track<T extends { dispose(): void }>(item: T): T {
    this.disposables.push(item);
    return item;
  }
}

/** A tree's position and size: `width` for the crown's or trunk's breadth, `height` for its height. */
function matrix(x: number, y: number, z: number, width: number, height: number): THREE.Matrix4 {
  return new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), NO_ROTATION, new THREE.Vector3(width, height, width));
}

function tree(key: string, district: string | null, x: number, z: number, ground: number): Tree {
  return {
    district,
    x,
    z,
    ground,
    // Hashing the key gives each tree its own stable size and shade of green.
    scale: 0.8 + hash01(`${key}:size`) * 0.5,
    color: GREENS[Math.floor(hash01(`${key}:green`) * GREENS.length)],
  };
}

function plazaTrees(city: CityLayout): Tree[] {
  const radius = city.plaza.size * 0.38;
  return Array.from({ length: PLAZA_TREES }, (_, i) => {
    const angle = (i / PLAZA_TREES) * Math.PI * 2 + Math.PI / PLAZA_TREES;
    return tree(`plaza:${i}`, null, Math.cos(angle) * radius, Math.sin(angle) * radius, 0.3);
  });
}

function districtTrees(city: CityLayout, district: CityLayout["districts"][number]): Tree[] {
  const footprints = city.buildings.filter((building) => building.district === district.language);
  const isFree = (x: number, z: number) =>
    footprints.every(
      (b) =>
        Math.abs(x - b.x) > b.size / 2 + BUILDING_CLEARANCE || Math.abs(z - b.z) > b.size / 2 + BUILDING_CLEARANCE,
    );
  const halfW = district.width / 2;
  const halfD = district.depth / 2;
  const trees: Tree[] = [];
  const add = (key: string, x: number, z: number) => {
    if (isFree(x, z)) {
      trees.push(tree(`${district.language}:${key}`, district.language, x, z, 0.2));
    }
  };

  // Sidewalk rows along all four edges.
  const edge = (length: number) => Math.max(1, Math.round(length / SIDEWALK_SPACING));
  const insetW = halfW - SIDEWALK_INSET;
  const insetD = halfD - SIDEWALK_INSET;
  for (let i = 0; i <= edge(district.width); i++) {
    const x = district.x - insetW + (2 * insetW * i) / edge(district.width);
    add(`n${i}`, x, district.z - insetD);
    add(`s${i}`, x, district.z + insetD);
  }
  for (let i = 1; i < edge(district.depth); i++) {
    const z = district.z - insetD + (2 * insetD * i) / edge(district.depth);
    add(`w${i}`, district.x - insetW, z);
    add(`e${i}`, district.x + insetW, z);
  }

  // Groves: some of the grid points inside the sidewalk that are clear of buildings.
  for (let x = -halfW + GROVE_SPACING; x <= halfW - GROVE_SPACING; x += GROVE_SPACING) {
    for (let z = -halfD + GROVE_SPACING; z <= halfD - GROVE_SPACING; z += GROVE_SPACING) {
      const key = `g${Math.round(x)}:${Math.round(z)}`;
      if (hash01(`${district.language}:${key}`) < GROVE_SHARE) {
        // A small nudge, so groves don't look planted on a grid.
        const nudge = (hash01(`${key}:nudge`) - 0.5) * 2;
        add(key, district.x + x + nudge, district.z + z - nudge);
      }
    }
  }
  return trees;
}
