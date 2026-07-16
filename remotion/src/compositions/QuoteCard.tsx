import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, QuoteCardProps } from "../schema";

// 金句卡：中段把一句核心观点放大定格。压暗背景 + 大引号 + 遮罩揭示入场
// （借鉴 Remotion 官网"遮罩揭示"：自下而上揭开，金句"揭晓"的仪式感比淡入强），
// 停留期间轻微持续放大（呼吸感），尾段整体淡出。
export const QuoteCard: React.FC<QuoteCardProps> = ({ text, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const isPortrait = height > width;
  const fontSize = 64 * s * (isPortrait ? 1.35 : 1);

  // spring 驱动揭示进度：顶部 inset 从 100% 收到 0，可见区自下而上生长。
  const enter = spring({ frame, fps, config: { damping: 16, mass: 0.6 } });
  const revealInset = interpolate(enter, [0, 1], [100, 0]);
  const drift = 1 + 0.015 * (frame / Math.max(1, durationInFrames));
  const opacityIn = interpolate(frame, [0, 6], [0, 1], { extrapolateRight: "clamp" });
  const outStart = durationInFrames - 8;
  const opacityOut = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        fontFamily: FONT_STACK,
        justifyContent: "center",
        alignItems: "center",
        opacity: Math.min(opacityIn, opacityOut),
      }}
    >
      <div style={{ position: "absolute", inset: 0, background: "rgba(8,8,12,0.62)" }} />
      <div
        style={{
          position: "relative",
          transform: `scale(${drift})`,
          clipPath: `inset(${revealInset}% 0 0 0)`,
          maxWidth: isPortrait ? width * 0.82 : width * 0.62,
          textAlign: "center",
        }}
      >
        <div
          style={{
            fontSize: fontSize * 2.2,
            fontWeight: 900,
            color: accent,
            lineHeight: 0.6,
            opacity: 0.9,
          }}
        >
          “
        </div>
        <div
          style={{
            fontSize,
            fontWeight: 800,
            color: "#f4f0e6",
            lineHeight: 1.5,
            textShadow: `0 ${6 * s}px ${30 * s}px rgba(0,0,0,0.65)`,
          }}
        >
          {text}
        </div>
        <div
          style={{
            margin: `${22 * s}px auto 0`,
            width: 120 * s,
            height: 4 * s,
            background: accent,
            borderRadius: 2 * s,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
