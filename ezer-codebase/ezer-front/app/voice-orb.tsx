"use client";

import { useEffect, useRef } from "react";

/**
 * Sphère de Fibonacci animée : l'unique indicateur d'état de la conversation.
 *
 * Trois régimes, repris du prototype : au repos les points sont ternes et la rotation lente ;
 * pendant que l'utilisateur parle ils scintillent ; pendant qu'Ezer répond des arcs électriques
 * relient les points proches.
 */

export type OrbSpeaker = "idle" | "user" | "assistant";

const POINT_COUNT = 150;
const MAX_PULSES = 14;

interface Point {
  x: number;
  y: number;
  z: number;
  phase: number;
  speed: number;
}

interface Pulse {
  a: number;
  b: number;
  life: number;
}

function fibonacciSphere(): Point[] {
  const points: Point[] = [];
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let index = 0; index < POINT_COUNT; index += 1) {
    const y = 1 - (index / (POINT_COUNT - 1)) * 2;
    const radius = Math.sqrt(1 - y * y);
    const theta = goldenAngle * index;
    points.push({
      x: Math.cos(theta) * radius,
      y,
      z: Math.sin(theta) * radius,
      // Le scintillement est déterministe par point : il ne dépend pas du temps de montage.
      phase: (index * 2.399963) % (Math.PI * 2),
      speed: 2 + ((index * 7) % 40) / 10,
    });
  }
  return points;
}

export function VoiceOrb({
  accent,
  size,
  speaker,
  onClick,
}: {
  accent: string;
  size: number;
  speaker: OrbSpeaker;
  onClick?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const speakerRef = useRef<OrbSpeaker>(speaker);

  // La boucle d'animation lit l'état courant sans être relancée à chaque changement.
  useEffect(() => {
    speakerRef.current = speaker;
  }, [speaker]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const context = canvas.getContext("2d");
    if (context === null) return;

    const points = fibonacciSphere();
    const pulses: Pulse[] = [];
    let rotationY = 0;
    let rotationX = 0;
    let elapsed = 0;
    let frame = 0;

    const draw = () => {
      frame = requestAnimationFrame(draw);
      const current = speakerRef.current;
      const active = current !== "idle";
      const answering = current === "assistant";
      const width = canvas.width;
      const height = canvas.height;

      elapsed += 0.016;
      rotationY += active ? (answering ? 0.005 : 0.002) : 0.001;
      rotationX = Math.sin(elapsed * 0.18) * 0.45;
      context.clearRect(0, 0, width, height);

      const sphereRadius = width * 0.36;
      const focal = width * 1.4;
      const centerX = width / 2;
      const centerY = height / 2;
      const cosX = Math.cos(rotationX);
      const sinX = Math.sin(rotationX);
      const cosY = Math.cos(rotationY);
      const sinY = Math.sin(rotationY);

      const projected = points.map((point) => {
        const x = point.x * cosY + point.z * sinY;
        const depth = -point.x * sinY + point.z * cosY;
        const y = point.y * cosX - depth * sinX;
        const z = point.y * sinX + depth * cosX;
        const scale = focal / (focal + z * sphereRadius);
        return {
          screenX: centerX + x * sphereRadius * scale,
          screenY: centerY + y * sphereRadius * scale,
          scale,
          point,
        };
      });

      if (answering) {
        if (Math.random() < 0.35 && pulses.length < MAX_PULSES) {
          const from = Math.floor(Math.random() * points.length);
          let best = -1;
          let bestDistance = Number.POSITIVE_INFINITY;
          for (let index = 0; index < points.length; index += 1) {
            if (index === from) continue;
            const candidate = points[index];
            const origin = points[from];
            if (candidate === undefined || origin === undefined) continue;
            const distance =
              (candidate.x - origin.x) ** 2 +
              (candidate.y - origin.y) ** 2 +
              (candidate.z - origin.z) ** 2;
            if (distance < bestDistance && distance > 0.02 && Math.random() < 0.4) {
              bestDistance = distance;
              best = index;
            }
          }
          if (best >= 0 && bestDistance < 0.5) pulses.push({ a: from, b: best, life: 1 });
        }
        for (const pulse of pulses) {
          pulse.life -= 0.045;
          const from = projected[pulse.a];
          const to = projected[pulse.b];
          if (from === undefined || to === undefined) continue;
          const alpha = Math.max(0, pulse.life) * 0.9 * Math.min(from.scale, to.scale);
          context.strokeStyle = `rgba(255,90,110,${alpha.toFixed(3)})`;
          context.lineWidth = 2.4 * pulse.life;
          context.beginPath();
          context.moveTo(from.screenX, from.screenY);
          context.lineTo(to.screenX, to.screenY);
          context.stroke();
        }
        for (let index = pulses.length - 1; index >= 0; index -= 1) {
          if ((pulses[index]?.life ?? 0) <= 0) pulses.splice(index, 1);
        }
      } else {
        pulses.length = 0;
      }

      for (const item of projected) {
        const depth = (item.scale - 0.8) * 3;
        let alpha: number;
        let radius: number;
        if (!active) {
          alpha = 0.22 * item.scale;
          radius = 1.4;
        } else if (answering) {
          alpha = 0.35 + 0.4 * Math.max(0, depth);
          radius = 1.6;
        } else {
          const twinkle = 0.5 + 0.5 * Math.sin(elapsed * item.point.speed + item.point.phase);
          alpha = (0.12 + 0.85 * twinkle * twinkle) * item.scale;
          radius = 1.2 + 1.6 * twinkle;
        }
        context.fillStyle =
          active && !answering && alpha > 0.7
            ? `rgba(255,150,160,${alpha.toFixed(3)})`
            : `rgba(196,46,66,${Math.min(1, alpha).toFixed(3)})`;
        context.beginPath();
        context.arc(item.screenX, item.screenY, radius * 2 * item.scale, 0, Math.PI * 2);
        context.fill();
      }
    };

    draw();
    return () => cancelAnimationFrame(frame);
  }, []);

  const label =
    speaker === "assistant"
      ? "Ezer répond"
      : speaker === "user"
        ? "Ezer vous écoute"
        : "Ezer en pause";

  return (
    <canvas
      aria-label={label}
      className="ezer-orb"
      height={size * 2}
      onClick={onClick}
      ref={canvasRef}
      role="img"
      style={{ width: size, height: size, borderRadius: "50%", outlineColor: accent }}
      width={size * 2}
    />
  );
}
