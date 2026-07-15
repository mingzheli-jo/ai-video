import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, GoldenCardProps } from "../schema";

// 金句全屏卡：视觉完全参考 OpeningCard——不透明黑底盖住画面、大号白字红描边居中、
// 下方细分隔线从中心展开；无要点行。用于 kind=golden 的核心句强调，比弹词更有分量。
// 红描边固定高冲击色，不跟 accent，保持冷开场同款视觉冲击感。
const GOLDEN_STROKE_COLOR = "#e23b3b";

// 字号自动缩放：以 14 字为基准，超出时等比缩小，避免长金句溢出屏幕。
const BASE_MAX_CHARS = 14;

export const GoldenCard: React.FC<GoldenCardProps> = ({ text, accent }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  // 等比缩放因子：以 1920 宽为基准，竖屏时同步缩小。
  const s = width / 1920;
  const isPortrait = height > width;
  const baseSize = 150 * s * (isPortrait ? 1.35 : 1);
  // 超过 14 字时线性缩字号，保证最长金句也能居中显示。
  const charCount = text.length;
  const sizeRatio = charCount > BASE_MAX_CHARS ? BASE_MAX_CHARS / charCount : 1;
  const titleSize = baseSize * sizeRatio;
  const stroke = Math.max(2, 7 * s * sizeRatio);

  // 主文字 spring 缩放入场 + 快速淡入（同 OpeningCard 参数，保持视觉一致性）。
  const enter = spring({ frame, fps, config: { damping: 14, mass: 0.6 } });
  const titleScale = interpolate(enter, [0, 1], [0.6, 1]);
  const titleOpacity = interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" });

  // 分隔线从中心向两侧展开。
  const lineProgress = spring({ frame: frame - 6, fps, config: { damping: 200, mass: 0.5 } });
  const lineWidth = interpolate(lineProgress, [0, 1], [0, width * 0.36]);

  return (
    <AbsoluteFill
      style={{
        background: "#000000",
        justifyContent: "center",
        alignItems: "center",
        fontFamily: FONT_STACK,
      }}
    >
      <div
        style={{
          transform: `scale(${titleScale})`,
          opacity: titleOpacity,
          fontSize: titleSize,
          fontWeight: 900,
          color: "#ffffff",
          letterSpacing: 4 * s * sizeRatio,
          WebkitTextStroke: `${stroke}px ${GOLDEN_STROKE_COLOR}`,
          paintOrder: "stroke fill",
          textShadow: `0 ${6 * s}px ${40 * s}px rgba(0,0,0,0.6)`,
          textAlign: "center",
          padding: `0 ${24 * s}px`,
          wordBreak: "break-all",
        }}
      >
        {text}
      </div>
      <div
        style={{
          height: 3 * s,
          width: lineWidth,
          margin: `${34 * s}px 0 0`,
          background: accent,
          boxShadow: `0 0 ${20 * s}px ${accent}`,
        }}
      />
    </AbsoluteFill>
  );
};
