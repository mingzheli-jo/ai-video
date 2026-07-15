import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { ChapterCardProps, FONT_STACK } from "../schema";

// 章节大字浮现（2026-07-15 用户点名：黄底横条退役，视觉统一成"描边大字浮在画面上"）：
// 透明背景，细金线从中心展开，节标题大字金色粗黑描边 spring 浮现，左上小号序号淡入。
// 参考对标博主的节标题打法与本片 intro 同款视觉语言。
const STROKE_COLOR = "#141410";

export const ChapterCard: React.FC<ChapterCardProps> = ({
  index,
  title,
  accent,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放：以 1920 宽为基准；竖屏加大字号。
  const s = width / 1920;
  const isPortrait = height > width;
  // 长标题整体收字号：超过 10 字按比例缩，保证单行不折行。
  const shrink = title.length > 10 ? 10 / title.length : 1;
  const fontSize = 92 * s * (isPortrait ? 1.3 : 1) * shrink;
  const stroke = Math.max(2, 5 * s);

  // 标题 spring 浮现（自下轻微上移 + 缩放定格）。
  const enter = spring({ frame, fps, config: { damping: 14, mass: 0.6 } });
  const titleOpacity = interpolate(enter, [0, 1], [0, 1]);
  const titleY = interpolate(enter, [0, 1], [40 * s, 0]);
  const titleScale = interpolate(enter, [0, 1], [0.85, 1]);

  // 细金线从中心向两侧展开（在标题上方）。
  const lineProgress = spring({ frame: frame - 4, fps, config: { damping: 200, mass: 0.5 } });
  const lineWidth = interpolate(lineProgress, [0, 1], [0, width * 0.3]);

  // 序号淡入（小号、半透明，跟在金线上方居中）。
  const indexOpacity = interpolate(frame, [6, 14], [0, 0.75], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // 尾段整体淡出，正片画面干净接管。
  const outStart = durationInFrames - 8;
  const groupOpacity = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ fontFamily: FONT_STACK }}>
      <div
        style={{
          position: "absolute",
          left: "50%",
          // 居中偏上：底部让给字幕；竖屏更靠上避开画面主体。
          top: isPortrait ? "24%" : "32%",
          transform: "translateX(-50%)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 14 * s,
          opacity: groupOpacity,
        }}
      >
        <div
          style={{
            opacity: indexOpacity,
            fontSize: 34 * s * (isPortrait ? 1.3 : 1),
            fontWeight: 700,
            color: "#ffffff",
            letterSpacing: 6 * s,
            textShadow: `0 ${3 * s}px ${16 * s}px rgba(0,0,0,0.6)`,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {String(index).padStart(2, "0")}
        </div>
        <div
          style={{
            height: 3 * s,
            width: lineWidth,
            background: accent,
            boxShadow: `0 0 ${16 * s}px ${accent}`,
          }}
        />
        <div
          style={{
            opacity: titleOpacity,
            transform: `translateY(${titleY}px) scale(${titleScale})`,
            fontSize,
            fontWeight: 900,
            color: accent,
            letterSpacing: 3 * s,
            WebkitTextStroke: `${stroke}px ${STROKE_COLOR}`,
            paintOrder: "stroke fill",
            textShadow: `0 ${5 * s}px ${28 * s}px rgba(0,0,0,0.55)`,
            textAlign: "center",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </div>
      </div>
    </AbsoluteFill>
  );
};
