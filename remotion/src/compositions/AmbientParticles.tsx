import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { AmbientParticlesProps } from "../schema";

// 低密度氛围粒子（借鉴 Remotion 官网"粒子效果"，2026-07-16 用户拍板）：金色微尘
// 缓缓上浮，低透明度给画面加质感、不抢戏。默认关、按选题开（密度一高就俗）。
//
// 无缝循环设计：本组件只渲染一小段（Python 侧 AMBIENT_LOOP_SECONDS），由 ffmpeg
// -stream_loop 循环铺满全片。因此所有运动必须以合成时长为周期：粒子行程
// p = (t*cycles + phase) % 1，cycles 取整数保证首尾帧完全一致；行程两端淡出，
// y 从底回卷到顶时粒子已不可见，循环接缝不可察觉。
const PARTICLE_COUNT = 22;

// 确定性伪随机（渲染必须逐帧可复现，不能用 Math.random）：经典 sin-hash。
const rand = (i: number, salt: number): number => {
  const x = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return x - Math.floor(x);
};

export const AmbientParticles: React.FC<AmbientParticlesProps> = ({ accent }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const t = frame / Math.max(1, durationInFrames);

  return (
    <AbsoluteFill>
      {Array.from({ length: PARTICLE_COUNT }, (_, i) => {
        const cycles = 1 + Math.floor(rand(i, 1) * 2); // 每循环走 1~2 趟（整数=无缝）
        const phase = rand(i, 2);
        const p = (t * cycles + phase) % 1;

        const size = (2 + rand(i, 3) * 4) * s;
        const baseX = rand(i, 4) * width;
        const sway = (14 + rand(i, 5) * 26) * s;
        const x = baseX + sway * Math.sin(2 * Math.PI * (p * 2 + rand(i, 6)));
        const y = (height + 60 * s) - p * (height + 120 * s);

        // 行程两端淡入淡出：回卷瞬间粒子必然透明，循环无接缝。
        const edgeFade = Math.min(1, Math.min(p, 1 - p) / 0.12);
        const opacity = (0.15 + rand(i, 7) * 0.25) * edgeFade;

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              transform: `translate(${x}px, ${y}px)`,
              width: size,
              height: size,
              borderRadius: "50%",
              background: accent,
              opacity,
              filter: `blur(${Math.max(0.5, size * 0.25)}px)`,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};
