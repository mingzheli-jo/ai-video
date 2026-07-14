import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, NumberPopProps } from "../schema";

// 数字强调：文案里的关键数字（"3步"“80%”）大字弹出，带过冲回弹，短促淡出。
// 摆在竖屏中上部偏右（避开章节卡的左上与底部字幕）。
export const NumberPop: React.FC<NumberPopProps> = ({ value, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const isPortrait = height > width;
  const fontSize = 150 * s * (isPortrait ? 1.35 : 1);

  const enter = spring({ frame, fps, config: { damping: 10, mass: 0.5, stiffness: 120 } });
  const scale = interpolate(enter, [0, 1], [0.3, 1]);
  const opacityIn = interpolate(frame, [0, 4], [0, 1], { extrapolateRight: "clamp" });
  const outStart = durationInFrames - 6;
  const opacityOut = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ fontFamily: FONT_STACK }}>
      <div
        style={{
          position: "absolute",
          right: isPortrait ? "8%" : "12%",
          top: isPortrait ? "34%" : "24%",
          transform: `scale(${scale})`,
          transformOrigin: "center",
          opacity: Math.min(opacityIn, opacityOut),
          fontSize,
          fontWeight: 900,
          color: accent,
          letterSpacing: 2 * s,
          textShadow: `0 ${8 * s}px ${36 * s}px rgba(0,0,0,0.6), 0 0 ${18 * s}px rgba(0,0,0,0.4)`,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
    </AbsoluteFill>
  );
};
