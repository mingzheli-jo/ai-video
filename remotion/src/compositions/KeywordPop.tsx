import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, KeywordPopProps } from "../schema";

// 关键词弹出（博主风格升级）：占屏宽 60-80% 的大字，黑色描边提可读性，
// 三色轮换填字（红/黄/白，由 Python 侧按全片动效序号写入 color prop），
// 视觉分量对标 OpeningCard 主标题级别，弹性入场保留低阻尼过冲回弹手感。
export const KeywordPop: React.FC<KeywordPopProps> = ({ keyword, color }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放因子：以 1920 宽为基准，CLI 用底片实际尺寸覆盖 composition，竖屏时同步缩小。
  const s = width / 1920;
  const isPortrait = height > width;
  // 字号对标 OpeningCard（150px baseline），竖屏额外放大 35%，目标占屏宽 60-80%。
  const fontSize = 150 * s * (isPortrait ? 1.35 : 1);
  // 黑色描边：高冲击力，无论底图深浅都能清晰显字。
  const strokeWidth = Math.max(3, 8 * s);
  const strokeColor = "#000000";

  // 弹性入场：低阻尼 spring 让缩放略微过冲再回弹（对标博主同款蹦字手感）。
  const enter = spring({ frame, fps, config: { damping: 9, mass: 0.6, stiffness: 130 } });
  const scale = interpolate(enter, [0, 1], [0.2, 1]);
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
          left: "50%",
          top: isPortrait ? "30%" : "26%",
          transform: `translate(-50%, -50%) scale(${scale})`,
          transformOrigin: "center",
          opacity: Math.min(opacityIn, opacityOut),
          fontSize,
          fontWeight: 900,
          // color 由 Python 侧三色轮换写入（红 #e74c3c / 黄 #f1c40f / 白 #ffffff）。
          color: color ?? "#ffffff",
          letterSpacing: 2 * s,
          WebkitTextStroke: `${strokeWidth}px ${strokeColor}`,
          // paintOrder=stroke fill：先描边后填字，彩色字体不被描边吃掉。
          paintOrder: "stroke fill",
          textShadow: `0 ${10 * s}px ${40 * s}px rgba(0,0,0,0.7)`,
          whiteSpace: "nowrap",
        }}
      >
        {keyword}
      </div>
    </AbsoluteFill>
  );
};
