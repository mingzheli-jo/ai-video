import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, IntroProps } from "../schema";

// 大字号标题 + 细线扫入。深底金字，透明背景（AbsoluteFill 不填底色）。
export const Intro: React.FC<IntroProps> = ({ title, subtitle, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放因子：以 1920 宽为基准。渲染时 CLI 用底片实际尺寸覆盖 composition，
  // s=1（1920 宽）时与旧版逐像素一致，竖屏（1080 宽）时所有固定 px 同步缩小。
  const s = width / 1920;
  const isPortrait = height > width;
  // 竖屏手机全屏观看，正文字号按惯例额外放大。
  const titleFontSize = 132 * s * (isPortrait ? 1.35 : 1);

  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.6 } });
  const titleY = interpolate(enter, [0, 1], [60 * s, 0]);
  const titleOpacity = interpolate(enter, [0, 1], [0, 1]);

  // 细线从中心向两侧扫入。
  const lineProgress = spring({
    frame: frame - 6,
    fps,
    config: { damping: 200, mass: 0.5 },
  });
  const lineWidth = interpolate(lineProgress, [0, 1], [0, width * 0.32]);

  // 尾段整体淡出，避免硬切。
  const outStart = durationInFrames - 10;
  const outOpacity = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        fontFamily: FONT_STACK,
        opacity: outOpacity,
      }}
    >
      <div style={{ textAlign: "center", opacity: titleOpacity }}>
        <div
          style={{
            height: 3 * s,
            width: lineWidth,
            margin: `0 auto ${40 * s}px`,
            background: accent,
            boxShadow: `0 0 ${24 * s}px ${accent}`,
          }}
        />
        <div
          style={{
            transform: `translateY(${titleY}px)`,
            fontSize: titleFontSize,
            fontWeight: 800,
            letterSpacing: 4 * s,
            color: accent,
            textShadow: `0 ${6 * s}px ${40 * s}px rgba(0,0,0,0.55)`,
          }}
        >
          {title}
        </div>
        {subtitle ? (
          <div
            style={{
              marginTop: 28 * s,
              fontSize: 42 * s * (isPortrait ? 1.35 : 1),
              fontWeight: 400,
              letterSpacing: 6 * s,
              color: "#f4f0e6",
              opacity: interpolate(enter, [0.4, 1], [0, 0.9], {
                extrapolateLeft: "clamp",
              }),
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
