import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, KeyPointsProps, LINE_PALETTE } from "../schema";

// 开屏要点卡：本期关键内容一行一行浮现，每行不同颜色（LINE_PALETTE 循环，
// 首色被 accent 覆盖保持品牌统一）。半透明深色底压住画面，保证行文字可读。
export const KeyPoints: React.FC<KeyPointsProps> = ({ lines, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const isPortrait = height > width;
  const fontSize = 56 * s * (isPortrait ? 1.35 : 1);
  const perLineDelay = Math.max(
    6,
    Math.floor((durationInFrames - 20) / Math.max(1, lines.length))
  );

  const outStart = durationInFrames - 8;
  const cardOpacity = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const palette = [accent, ...LINE_PALETTE.slice(1)];

  return (
    <AbsoluteFill style={{ fontFamily: FONT_STACK, opacity: cardOpacity }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(10,10,14,0.55)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: isPortrait ? "30%" : "50%",
          transform: "translate(-50%, -50%)",
          display: "flex",
          flexDirection: "column",
          gap: 26 * s,
          maxWidth: isPortrait ? width * 0.86 : width * 0.6,
        }}
      >
        {lines.map((line, i) => {
          const enter = spring({
            frame: frame - i * perLineDelay,
            fps,
            config: { damping: 200, mass: 0.5 },
          });
          const x = interpolate(enter, [0, 1], [40 * s, 0]);
          const opacity = interpolate(enter, [0, 1], [0, 1]);
          const color = palette[i % palette.length];
          return (
            <div
              key={i}
              style={{
                transform: `translateX(${x}px)`,
                opacity,
                display: "flex",
                alignItems: "center",
                gap: 20 * s,
              }}
            >
              <div
                style={{
                  width: 10 * s,
                  height: fontSize * 0.9,
                  background: color,
                  borderRadius: 5 * s,
                  flex: "0 0 auto",
                }}
              />
              <div
                style={{
                  fontSize,
                  fontWeight: 800,
                  color,
                  textShadow: `0 ${4 * s}px ${24 * s}px rgba(0,0,0,0.6)`,
                }}
              >
                {line}
              </div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
