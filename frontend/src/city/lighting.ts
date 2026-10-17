import * as THREE from "three";

/** Anything in the city that looks different at night (glowing windows, lights, ...). */
export interface NightAware {
  /** 0 = full day, 1 = full night; values in between during the transition. */
  setNight(amount: number): void;
}

// Each look is a set of values; switching between day and night blends from one to the other.
const DAY = {
  sky: new THREE.Color("#8ec5ff"),
  hemiSky: new THREE.Color("#dcefff"),
  hemiGround: new THREE.Color("#3b4148"),
  hemiIntensity: 1.1,
  sunColor: new THREE.Color("#fff3df"),
  sunIntensity: 2.6,
};
// Night keeps enough blue-ish ambient light that every building's color stays readable; the lit
// windows still stand out because they glow on top of it.
const NIGHT = {
  sky: new THREE.Color("#05070f"),
  hemiSky: new THREE.Color("#3a4f86"),
  hemiGround: new THREE.Color("#1a2030"),
  hemiIntensity: 1.0,
  sunColor: new THREE.Color("#a9bcff"), // Moonlight.
  sunIntensity: 0.9,
};
const TRANSITION_SECONDS = 1.2;

/**
 * Day/night lighting: a hemisphere light for soft ambient color, plus one directional light
 * that is the sun by day and the moon by night (and casts the shadows).
 */
export class DayNight {
  readonly hemisphere = new THREE.HemisphereLight();
  readonly sun = new THREE.DirectionalLight();
  private readonly fog: THREE.Fog;
  private night = 1; // Start at night: that's when the activity shows.
  private target = 1;
  private readonly targets: NightAware[] = [];
  private radius = 100;

  constructor(private readonly scene: THREE.Scene) {
    this.fog = new THREE.Fog(NIGHT.sky, 200, 1000);
    scene.fog = this.fog;
    scene.background = NIGHT.sky.clone();

    this.sun.castShadow = true;
    // 1024 texels are plenty for a city seen from above, at a quarter of 2048's cost.
    this.sun.shadow.mapSize.set(1024, 1024);
    this.sun.shadow.bias = -0.0005;
    scene.add(this.hemisphere, this.sun, this.sun.target);
  }

  get isNight(): boolean {
    return this.target === 1;
  }

  /** Fit the fog and the shadow area to a city of this radius; register its night-aware parts. */
  fitCity(radius: number, targets: NightAware[]): void {
    this.radius = radius;
    this.targets.length = 0;
    this.targets.push(...targets);

    const shadowCamera = this.sun.shadow.camera;
    shadowCamera.left = shadowCamera.bottom = -radius * 1.2;
    shadowCamera.right = shadowCamera.top = radius * 1.2;
    shadowCamera.near = 1;
    shadowCamera.far = radius * 6;
    shadowCamera.updateProjectionMatrix();

    this.fog.near = radius * 1.8;
    this.fog.far = radius * 6;
    this.apply();
  }

  toggle(): void {
    this.target = this.target === 1 ? 0 : 1;
  }

  /**
   * Move a little toward the target look. Call once per frame with the elapsed seconds.
   * Returns true while the look is changing (so the view knows to draw a new frame).
   */
  update(deltaSeconds: number): boolean {
    if (this.night === this.target) {
      return false;
    }
    const step = deltaSeconds / TRANSITION_SECONDS;
    this.night =
      this.target > this.night
        ? Math.min(this.target, this.night + step)
        : Math.max(this.target, this.night - step);
    this.apply();
    return true;
  }

  private apply(): void {
    // Smoothstep eases in and out, so the change doesn't start or stop abruptly.
    const t = THREE.MathUtils.smoothstep(this.night, 0, 1);
    const sky = DAY.sky.clone().lerp(NIGHT.sky, t);
    (this.scene.background as THREE.Color).copy(sky);
    this.fog.color.copy(sky);

    this.hemisphere.color.copy(DAY.hemiSky).lerp(NIGHT.hemiSky, t);
    this.hemisphere.groundColor.copy(DAY.hemiGround).lerp(NIGHT.hemiGround, t);
    this.hemisphere.intensity = THREE.MathUtils.lerp(DAY.hemiIntensity, NIGHT.hemiIntensity, t);

    this.sun.color.copy(DAY.sunColor).lerp(NIGHT.sunColor, t);
    this.sun.intensity = THREE.MathUtils.lerp(DAY.sunIntensity, NIGHT.sunIntensity, t);
    // Both the sun and the moon sit on the same side as the starting camera (+x, +z), so the
    // walls you first look at are the lit ones; the moon is lower and further south.
    const r = this.radius;
    const daySpot = new THREE.Vector3(r * 0.9, r * 1.6, r * 0.7);
    const nightSpot = new THREE.Vector3(r * 0.4, r * 1.3, r * 0.9);
    this.sun.position.copy(daySpot.lerp(nightSpot, t));

    for (const target of this.targets) {
      target.setNight(t);
    }
  }
}
