import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, OpeningCardProps } from "../schema";

// 冷开场卡（复刻对标博主）：全屏纯黑底盖住底片开头，中央大号白字红描边主题词，
// 下方分隔线从中心展开，再两行小字要点依次淡入。尾段整体淡出，与 intro 交叠淡入衔接。
// 红描边用固定高冲击色，不跟 accent（冷开场要的就是这一下红黑对比的视觉冲击）。
const OPENING_STROKE_COLOR = "#e23b3b";

export const OpeningCard: React.FC<OpeningCardProps> = ({ title, points, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放因子：以 1920 宽为基准，CLI 用底片实际尺寸覆盖 composition，竖屏时同步缩小。
  const s = width / 1920;
  const isPortrait = height > width;
  const titleSize = 150 * s * (isPortrait ? 1.35 : 1);
  const pointSize = 40 * s * (isPortrait ? 1.35 : 1);
  const stroke = Math.max(2, 7 * s);

  // 主题词 spring 缩放入场 + 快速淡入。
  const enter = spring({ frame, fps, config: { damping: 14, mass: 0.6 } });
  const titleScale = interpolate(enter, [0, 1], [0.6, 1]);
  const titleOpacity = interpolate(frame, [0, 5], [0, 1], { extrapolateRight: "clamp" });

  // 分隔线从中心向两侧展开。
  const lineProgress = spring({ frame: frame - 6, fps, config: { damping: 200, mass: 0.5 } });
  const lineWidth = interpolate(lineProgress, [0, 1], [0, width * 0.36]);

  // 结尾硬切（2026-07-15 用户点名）：对标博主的开场卡就是干脆的黑卡直切正片，
  // 不做尾段淡出——淡出反而显得拖沓。
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
          letterSpacing: 4 * s,
          WebkitTextStroke: `${stroke}px ${OPENING_STROKE_COLOR}`,
          paintOrder: "stroke fill",
          textShadow: `0 ${6 * s}px ${40 * s}px rgba(0,0,0,0.6)`,
          textAlign: "center",
        }}
      >
        {title}
      </div>
      <div
        style={{
          height: 3 * s,
          width: lineWidth,
          margin: `${34 * s}px 0`,
          background: accent,
          boxShadow: `0 0 ${20 * s}px ${accent}`,
        }}
      />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 14 * s,
        }}
      >
        {points.map((line, i) => {
          const lineIn = spring({
            frame: frame - 12 - i * 6,
            fps,
            config: { damping: 200, mass: 0.5 },
          });
          const opacity = interpolate(lineIn, [0, 1], [0, 0.92]);
          const y = interpolate(lineIn, [0, 1], [12 * s, 0]);
          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `translateY(${y}px)`,
                fontSize: pointSize,
                fontWeight: 500,
                color: "#e8e4d8",
                letterSpacing: 2 * s,
              }}
            >
              {line}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
