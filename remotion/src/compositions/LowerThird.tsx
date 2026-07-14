import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, LowerThirdProps } from "../schema";

// 底部左侧胶囊条。深底白字，左侧 accent 竖条点缀。
export const LowerThird: React.FC<LowerThirdProps> = ({ text, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放因子：以 1920 宽为基准，s=1 时与旧版逐像素一致。
  const s = width / 1920;
  const isPortrait = height > width;
  // 竖屏底部安全区上抬（避开手机进度条/系统手势区），横屏沿用 120*s。
  const bottom = isPortrait ? height * 0.18 : 120 * s;
  const left = 96 * s;
  // 文字最大宽度：留出左内衬 + 右缘余量，防竖屏窄屏撞右缘。
  const textMaxWidth = width - left - 120 * s;

  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.5 } });
  const x = interpolate(enter, [0, 1], [-80 * s, 0]);
  const opacity = interpolate(enter, [0, 1], [0, 1]);

  const outStart = durationInFrames - 10;
  const outOpacity = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ fontFamily: FONT_STACK }}>
      <div
        style={{
          position: "absolute",
          left,
          bottom,
          maxWidth: textMaxWidth,
          transform: `translateX(${x}px)`,
          opacity: opacity * outOpacity,
          display: "flex",
          alignItems: "center",
          background: "rgba(18,17,13,0.82)",
          backdropFilter: `blur(${6 * s}px)`,
          padding: `${22 * s}px ${44 * s}px ${22 * s}px ${30 * s}px`,
          borderRadius: 999,
          boxShadow: `0 ${16 * s}px ${48 * s}px rgba(0,0,0,0.5)`,
        }}
      >
        <div
          style={{
            flexShrink: 0,
            width: 8 * s,
            height: 52 * s,
            borderRadius: 4 * s,
            background: accent,
            marginRight: 26 * s,
            boxShadow: `0 0 ${18 * s}px ${accent}`,
          }}
        />
        <div
          style={{
            fontSize: 52 * s * (isPortrait ? 1.35 : 1),
            fontWeight: 600,
            color: "#f6f3ea",
            letterSpacing: 1 * s,
          }}
        >
          {text}
        </div>
      </div>
    </AbsoluteFill>
  );
};
