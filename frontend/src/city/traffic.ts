import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { CityLayout } from "../api";
import { hash01 } from "./hash";
import type { NightAware } from "./lighting";

// Each district (and the plaza) has a one-way loop road just outside its edge, in the avenue.
// Every loop runs clockwise as seen from above, so in an avenue between two districts the two
// lanes go opposite ways, and traffic keeps to the right.
const LANE_OFFSET = 2.5; // Meters from the edge to the middle of the lane.
const CAR_SPACING = 11; // At most one car per this many meters of lane.
const MAX_CARS_PER_LOOP = 14;
const PLAZA_CARS = 4;
// Cars are drawn a quarter larger than life (5 m long), so they're visible from the usual
// camera distance.
const CAR_SCALE = 1.25;

const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0);
const UP = new THREE.Vector3(0, 1, 0);
const CAR_SIZE = new THREE.Vector3(CAR_SCALE, CAR_SCALE, CAR_SCALE);
const PAINT = ["#e6edf3", "#c9d1d9", "#1f2328", "#d1242f", "#2f6feb", "#f2cc60", "#1a7f37", "#8250df"].map(
  (hex) => new THREE.Color(hex),
);

/** One loop road: a rectangle (the edge it runs around) widened by LANE_OFFSET, rounded at the corners. */
interface Loop {
  district: string | null; // null for the plaza
  x0: number;
  z0: number;
  x1: number;
  z1: number;
  length: number;
  speed: number; // meters per second
  /** Indexes of this loop's car slots in the meshes, busiest-first (see setActivity). */
  slots: number[];
  /** How many of the slots have a car right now. */
  cars: number;
  /** The dashed line along the lane's outer edge (shown while the loop has cars). */
  marking: THREE.Line;
}

/**
 * Cars driving around the districts. How many drive around a district follows its recent work
 * (the same commits that light its windows), so busy parts of the city have busy streets.
 *
 * Each car is four instanced parts (body, windows, headlights, tail lights) sharing one matrix.
 * Moving them means redrawing every frame, so CityView only does that while cars are shown and
 * the visitor hasn't asked their system for reduced motion.
 */
export class TrafficLayer implements NightAware {
  readonly group = new THREE.Group();
  // First, so the fields below can register what they create.
  private readonly disposables: { dispose(): void }[] = [];
  private readonly loops: Loop[] = [];
  /** Every slot's place along its loop, as a share (0..1) of the loop's length. */
  private readonly phase: number[] = [];
  private readonly parts: THREE.InstancedMesh[];
  private readonly headlights: THREE.MeshBasicMaterial;
  private readonly markingMaterial = this.track(
    new THREE.LineDashedMaterial({ color: "#c9d1d9", dashSize: 3, gapSize: 3, transparent: true, opacity: 0.45 }),
  );
  private readonly matrix = new THREE.Matrix4();
  private readonly position = new THREE.Vector3();
  private readonly rotation = new THREE.Quaternion();

  constructor(city: CityLayout) {
    const half = city.plaza.size / 2;
    this.addLoop(null, -half, -half, half, half);
    for (const d of city.districts) {
      this.addLoop(d.language, d.x - d.width / 2, d.z - d.depth / 2, d.x + d.width / 2, d.z + d.depth / 2);
    }

    this.headlights = this.track(new THREE.MeshBasicMaterial({ color: "#fff4d6" }));
    const geometries = carGeometries();
    for (const geometry of Object.values(geometries)) this.track(geometry);
    const count = this.phase.length;
    this.parts = [
      this.part(geometries.body, new THREE.MeshStandardMaterial({ roughness: 0.45, metalness: 0.3 }), count),
      this.part(geometries.glass, new THREE.MeshStandardMaterial({ color: "#1b2230", roughness: 0.15 }), count),
      this.part(geometries.headlights, this.headlights, count),
      this.part(geometries.taillights, new THREE.MeshBasicMaterial({ color: "#ff3b30" }), count),
    ];
    this.phase.forEach((_, slot) => this.parts[0].setColorAt(slot, PAINT[Math.floor(hash01(`car:${slot}:paint`) * PAINT.length)]));
  }

  /** Whether any car is out (if not, nothing moves and nothing needs redrawing). */
  get hasCars(): boolean {
    return this.loops.some((loop) => loop.cars > 0);
  }

  /**
   * Put cars on each district's loop for how busy it is: `activity` maps a district to its
   * standing buildings and the sum of their window brightness (0..1 each). Districts not in the
   * map (not built yet) get no cars; the plaza gets a few once anything stands.
   */
  setActivity(activity: Map<string, { standing: number; brightness: number }>): void {
    let anything = false;
    for (const loop of this.loops) {
      if (loop.district === null) continue;
      const district = activity.get(loop.district);
      const cars = district && district.standing > 0 ? 1 + Math.round(district.brightness * 2.5) : 0;
      loop.cars = Math.min(cars, loop.slots.length);
      anything ||= cars > 0;
    }
    const plaza = this.loops.find((loop) => loop.district === null);
    if (plaza) plaza.cars = anything ? Math.min(PLAZA_CARS, plaza.slots.length) : 0;
    for (const loop of this.loops) loop.marking.visible = loop.cars > 0;
  }

  /** Move every car to where it is `seconds` after the start. */
  update(seconds: number): void {
    for (const loop of this.loops) {
      loop.slots.forEach((slot, rank) => {
        if (rank >= loop.cars) {
          for (const part of this.parts) part.setMatrixAt(slot, HIDDEN);
          return;
        }
        const distance = (this.phase[slot] * loop.length + seconds * loop.speed) % loop.length;
        const angle = pointOnLoop(loop, distance, this.position);
        this.rotation.setFromAxisAngle(UP, angle);
        this.matrix.compose(this.position, this.rotation, CAR_SIZE);
        for (const part of this.parts) part.setMatrixAt(slot, this.matrix);
      });
    }
    for (const part of this.parts) part.instanceMatrix.needsUpdate = true;
  }

  setNight(amount: number): void {
    // Headlights are dim by day and bright at night (a basic material isn't affected by lights).
    this.headlights.color.setRGB(1, 0.96, 0.84).multiplyScalar(THREE.MathUtils.lerp(0.55, 1.6, amount));
  }

  dispose(): void {
    for (const item of this.disposables) item.dispose();
  }

  private addLoop(district: string | null, x0: number, z0: number, x1: number, z1: number): void {
    const key = district ?? "plaza";
    const length = 2 * (x1 - x0 + (z1 - z0)) + 2 * Math.PI * LANE_OFFSET;
    const slotCount = Math.min(MAX_CARS_PER_LOOP, Math.floor(length / CAR_SPACING));
    // Slots sit at evenly spaced places, so cars on a loop never overlap. They're numbered by
    // stepping around the loop in strides of about 38% of it, so when only the first few slots
    // have cars, those cars are spread out rather than bunched together.
    const stride = coprimeStride(slotCount);
    const slots: number[] = [];
    for (let i = 0; i < slotCount; i++) {
      slots.push(this.phase.length);
      const jitter = (hash01(`${key}:${i}`) - 0.5) * 0.5; // Up to a quarter gap either way.
      this.phase.push((((i * stride) % slotCount) + jitter + slotCount) % slotCount / slotCount);
    }
    const speed = 6 + hash01(`${key}:speed`) * 4; // 22-36 km/h
    const loop: Loop = { district, x0, z0, x1, z1, length, speed, slots, cars: 0, marking: new THREE.Line() };
    loop.marking = this.marking(loop);
    this.loops.push(loop);
  }

  /**
   * Road markings: a dashed line along the lane's outer edge. Between two districts the edges
   * of their two lanes meet in the middle of the avenue, so this becomes its center line.
   */
  private marking(loop: Loop): THREE.Line {
    const offset = 2 * LANE_OFFSET;
    const length = 2 * (loop.x1 - loop.x0 + (loop.z1 - loop.z0)) + 2 * Math.PI * offset;
    const points: THREE.Vector3[] = [];
    for (let d = 0; d <= length; d += 1) {
      const point = new THREE.Vector3();
      pointOnLoop(loop, d, point, offset);
      points.push(point.setY(0.03)); // Just above the ground, so it isn't hidden in it.
    }
    const geometry = this.track(new THREE.BufferGeometry().setFromPoints(points));
    const line = new THREE.Line(geometry, this.markingMaterial);
    line.computeLineDistances(); // Dashed lines need to know how far along each point is.
    line.visible = false;
    this.group.add(line);
    return line;
  }

  private part(geometry: THREE.BufferGeometry, material: THREE.Material, count: number): THREE.InstancedMesh {
    this.track(material);
    const mesh = new THREE.InstancedMesh(geometry, material, Math.max(count, 1));
    mesh.count = count;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false; // Cars move, so fixed bounds would go stale.
    for (let slot = 0; slot < count; slot++) mesh.setMatrixAt(slot, HIDDEN);
    this.group.add(mesh);
    return mesh;
  }

  private track<T extends { dispose(): void }>(item: T): T {
    this.disposables.push(item);
    return item;
  }
}

/**
 * The point `distance` meters along a loop, `r` meters out from its edge, written into `out`,
 * and the car's heading there (as a rotation around the vertical axis). The loop: the north edge
 * going east, a quarter circle around the north-east corner, the east edge going south, and so
 * on, clockwise from above.
 */
function pointOnLoop(loop: Loop, distance: number, out: THREE.Vector3, r = LANE_OFFSET): number {
  const { x0, z0, x1, z1 } = loop;
  const corner = (Math.PI / 2) * r;
  const sides: { length: number; at: (d: number) => number }[] = [
    { length: x1 - x0, at: (d) => straight(x0 + d, z0 - r, 1, 0) },
    { length: corner, at: (d) => turn(x1, z0, -Math.PI / 2 + d / r) },
    { length: z1 - z0, at: (d) => straight(x1 + r, z0 + d, 0, 1) },
    { length: corner, at: (d) => turn(x1, z1, d / r) },
    { length: x1 - x0, at: (d) => straight(x1 - d, z1 + r, -1, 0) },
    { length: corner, at: (d) => turn(x0, z1, Math.PI / 2 + d / r) },
    { length: z1 - z0, at: (d) => straight(x0 - r, z1 - d, 0, -1) },
    { length: corner, at: (d) => turn(x0, z0, Math.PI + d / r) },
  ];

  function straight(x: number, z: number, dx: number, dz: number): number {
    out.set(x, 0, z);
    return heading(dx, dz);
  }
  // On a corner the car circles the district's corner at distance r; it faces along the circle.
  function turn(cx: number, cz: number, angle: number): number {
    out.set(cx + Math.cos(angle) * r, 0, cz + Math.sin(angle) * r);
    return heading(-Math.sin(angle), Math.cos(angle));
  }

  let left = distance;
  for (const side of sides) {
    if (left <= side.length) return side.at(left);
    left -= side.length;
  }
  return sides[0].at(0);
}

/** A step near 38% of `count` that shares no factor with it, so stepping by it visits every slot. */
function coprimeStride(count: number): number {
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  let stride = Math.max(1, Math.round(count * 0.382));
  while (gcd(stride, count) !== 1) stride++;
  return stride;
}

/**
 * The rotation around the vertical axis that turns a car (built facing +x) to face (dx, dz).
 * Three.js turns +x toward -z for positive angles, hence the minus sign.
 */
function heading(dx: number, dz: number): number {
  return Math.atan2(-dz, dx);
}

/** A small car facing +x, about 4 m long, made of boxes; one geometry per material. */
function carGeometries(): Record<"body" | "glass" | "headlights" | "taillights", THREE.BufferGeometry> {
  const box = (width: number, height: number, depth: number, x: number, y: number, z: number) =>
    new THREE.BoxGeometry(width, height, depth).translate(x, y, z);
  const pair = (x: number) => mergeGeometries([box(0.12, 0.22, 0.38, x, 0.66, -0.55), box(0.12, 0.22, 0.38, x, 0.66, 0.55)]);
  return {
    body: box(4, 0.85, 1.8, 0, 0.6, 0),
    glass: box(2.1, 0.62, 1.62, -0.25, 1.33, 0),
    headlights: pair(2.0),
    taillights: pair(-2.0),
  };
}
