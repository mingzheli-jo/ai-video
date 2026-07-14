import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { ChapterCardProps, FONT_STACK } from "../schema";

// 侧边滑入色块 + 序号。深底白字，色块用 accent。
export const ChapterCard: React.FC<ChapterCardProps> = ({
  index,
  title,
  accent,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放因子：以 1920 宽为基准，s=1 时与旧版逐像素一致。
  const s = width / 1920;
  const isPortrait = height > width;

  // 小幅滑入（约 160px，随分辨率缩放）+ 淡入淡出：卡片全程留在屏内、始终可读，
  // 不再像旧版那样整块滑出屏幕左侧半屏（竖屏窄，大幅滑动会让序号/标题长时间在屏外）。
  const slideDist = 160 * s;
  const slide = spring({ frame, fps, config: { damping: 200, mass: 0.5 } });
  const blockX = interpolate(slide, [0, 1], [-slideDist, 0]);
  const opacityIn = interpolate(frame, [0, 7], [0, 1], { extrapolateRight: "clamp" });

  const textReveal = spring({
    frame: frame - 5,
    fps,
    config: { damping: 200, mass: 0.5 },
  });
  const textX = interpolate(textReveal, [0, 1], [-60 * s, 0]);
  const textOpacity = interpolate(textReveal, [0, 1], [0, 1]);

  const outStart = durationInFrames - 8;
  const outX = interpolate(frame, [outStart, durationInFrames], [0, -slideDist], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacityOut = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cardOpacity = Math.min(opacityIn, opacityOut);

  return (
    <AbsoluteFill style={{ fontFamily: FONT_STACK }}>
      <div
        style={{
          position: "absolute",
          left: 0,
          // 竖屏摆上方 22%（避开画面主体与底部字幕）；横屏保持垂直居中不变。
          top: isPortrait ? "22%" : "50%",
          transform: `translate(${blockX + outX}px, -50%)`,
          opacity: cardOpacity,
          display: "flex",
          alignItems: "center",
          background: accent,
          padding: `${36 * s}px ${64 * s}px ${36 * s}px ${48 * s}px`,
          borderRadius: `0 ${20 * s}px ${20 * s}px 0`,
          boxShadow: `0 ${20 * s}px ${60 * s}px rgba(0,0,0,0.45)`,
        }}
      >
        <div
          style={{
            fontSize: 120 * s * (isPortrait ? 1.35 : 1),
            fontWeight: 900,
            lineHeight: 1,
            color: "rgba(20,18,12,0.35)",
            marginRight: 32 * s,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {String(index).padStart(2, "0")}
        </div>
        <div
          style={{
            transform: `translateX(${textX}px)`,
            opacity: textOpacity,
            fontSize: 64 * s * (isPortrait ? 1.35 : 1),
            fontWeight: 700,
            color: "#141410",
            maxWidth: (isPortrait ? width * 0.72 : 900) * (isPortrait ? 1 : s),
          }}
        >
          {title}
        </div>
      </div>
    </AbsoluteFill>
  );
};
