// Plays a city's music (the MusicParams JSON from the backend) with Tone.js.
//
// Tone.js basics: the Transport is a musical clock with a tempo. A Sequence is a list of
// events the Transport calls one by one at a fixed note length (here sixteenth notes),
// looping forever. Each call receives the exact audio-clock time the step should sound at.

import * as Tone from "tone";
import type { MusicParams, Track } from "../api";
import { createInstrument, type Instrument } from "./instruments";

/** How long a note rings for each track role. */
const NOTE_LENGTH: Record<string, Tone.Unit.Time> = {
  melody: "8n",
  bass: "8n",
  chords: "2n",
  arpeggio: "16n",
  drums: "16n",
};

// tempo_variation is 0 (steady committer) to 1 (bursty); it becomes up to this much swing.
const MAX_SWING = 0.5;

interface Voice {
  track: Track;
  instrument: Instrument;
  level: Tone.Volume;
}

export class MusicPlayer {
  private music: MusicParams | null = null;
  // Nothing touches the audio system until the first Play click: browsers only allow sound
  // after the user interacts with the page, and warn about audio created before that.
  private mixer: { input: Tone.Gain; nodes: Tone.ToneAudioNode[]; master: Tone.Volume } | null = null;
  private sequence: Tone.Sequence<number> | null = null;
  private voices: Voice[] = [];
  private volume = 0.7;

  get hasMusic(): boolean {
    return (this.music?.tracks.length ?? 0) > 0;
  }

  get isPlaying(): boolean {
    return this.mixer !== null && Tone.getTransport().state === "started";
  }

  /** Switch to new music. If music is already playing, the new music keeps playing. */
  load(music: MusicParams): void {
    this.music = music;
    if (this.mixer !== null) {
      const wasPlaying = this.isPlaying;
      this.rebuild();
      if (wasPlaying && this.hasMusic) {
        Tone.getTransport().start();
      }
    }
  }

  /** Start (or resume) playback. Must be called from a click or key handler. */
  async play(): Promise<void> {
    // Unlocks browser audio. This only works during a user gesture, hence the rule above.
    await Tone.start();
    if (this.mixer === null) {
      this.mixer = this.createMixer();
      this.rebuild();
    }
    if (this.hasMusic) {
      Tone.getTransport().start();
    }
  }

  /** Pause, keeping the position so play() continues from the same step. */
  pause(): void {
    if (this.mixer !== null) {
      Tone.getTransport().pause();
    }
  }

  /** 0 (silent) to 1 (full). */
  setVolume(fraction: number): void {
    this.volume = Math.min(Math.max(fraction, 0), 1);
    if (this.mixer !== null) {
      this.applyVolume(this.mixer.master);
    }
  }

  dispose(): void {
    this.clearVoices();
    if (this.mixer !== null) {
      for (const node of this.mixer.nodes) node.dispose();
      this.mixer = null;
    }
  }

  /**
   * The shared signal chain every track feeds into:
   * tracks -> input -> reverb (a little room sound) -> limiter (never clips) -> master volume.
   */
  private createMixer(): NonNullable<MusicPlayer["mixer"]> {
    const master = new Tone.Volume().toDestination();
    const limiter = new Tone.Limiter(-2).connect(master);
    const reverb = new Tone.Reverb({ decay: 3.5, wet: 0.22 }).connect(limiter);
    const input = new Tone.Gain(1).connect(reverb);
    this.applyVolume(master);
    return { input, master, nodes: [input, reverb, limiter, master] };
  }

  /** Throw away the current tracks and set up the ones in `this.music`. */
  private rebuild(): void {
    const transport = Tone.getTransport();
    transport.stop();
    transport.position = 0;
    this.clearVoices();
    if (this.mixer === null || this.music === null) {
      return;
    }
    const music = this.music;
    transport.bpm.value = music.tempo_bpm;
    // Swing delays every other sixteenth note a little: steady players sound straight and
    // even, bursty ones loose and groovy. It comes from the data, so it's always the same.
    transport.swing = music.tempo_variation * MAX_SWING;
    transport.swingSubdivision = "16n";

    const input = this.mixer.input;
    this.voices = music.tracks.map((track) => {
      const instrument = createInstrument(track.instrument);
      // The backend's volume is a 0..1 gain; Tone's Volume node works in decibels.
      const level = new Tone.Volume(Tone.gainToDb(track.volume)).connect(input);
      instrument.output.connect(level);
      return { track, instrument, level };
    });

    const steps = Array.from({ length: music.bars * music.steps_per_bar }, (_, step) => step);
    const voices = this.voices;
    this.sequence = new Tone.Sequence(
      (time, step) => {
        for (const voice of voices) {
          const notes = voice.track.steps[step];
          if (notes !== undefined && notes.length > 0) {
            voice.instrument.play(notes, NOTE_LENGTH[voice.track.role] ?? "8n", time);
          }
        }
      },
      steps,
      "16n",
    ).start(0);
  }

  private clearVoices(): void {
    this.sequence?.dispose();
    this.sequence = null;
    for (const voice of this.voices) {
      voice.instrument.dispose();
      voice.level.dispose();
    }
    this.voices = [];
  }

  private applyVolume(master: Tone.Volume): void {
    // gainToDb(0) is -Infinity, which can't be faded to, so silence is a mute instead.
    master.mute = this.volume === 0;
    if (this.volume > 0) {
      master.volume.rampTo(Tone.gainToDb(this.volume), 0.05);
    }
  }
}
