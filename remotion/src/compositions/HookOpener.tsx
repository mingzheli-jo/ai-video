import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, HOOK_LINE_COLORS, HookOpenerProps } from "../schema";

// 开屏 5 秒钩子序列（2026-07-15 用户点名，替代冷开场黑卡）：
// 透明背景叠在正片画面上，2~3 条钩子短句自下而上堆叠、逐条 spring 弹入，
// 大字粗黑描边 + 红/黄/白三色轮换——对标博主的首屏钩子打法，首帧即见画面不浪费黄金时间。
const LINE_STAGGER_FRAMES = 24; // 每条短句相隔 0.8s（30fps）依次入场
const STROKE_COLOR = "#141410";

export const HookOpener: React.FC<HookOpenerProps> = ({ lines, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  // 等比缩放：以 1920 宽为基准；竖屏加大字号（画面窄、观看距离近）。
  const s = width / 1920;
  const isPortrait = height > width;
  const shown = lines.slice(0, 3);
  // 最长行超 10 字整体收字号，保证单行放得下不换行。
  const longest = Math.max(...shown.map((l) => l.length), 1);
  const shrink = longest > 10 ? 10 / longest : 1;
  const fontSize = 108 * s * (isPortrait ? 1.3 : 1) * shrink;
  const stroke = Math.max(2, 6 * s);

  // 尾段整体淡出：钩子序列退场后正片画面干净接管。
  const outStart = durationInFrames - 10;
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
          // 居中偏上：底部留给字幕，中心留给画面主体。
          top: isPortrait ? "30%" : "34%",
          transform: "translateX(-50%)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 18 * s,
          opacity: groupOpacity,
          width: "92%",
        }}
      >
        {shown.map((line, i) => {
          const enter = spring({
            frame: frame - i * LINE_STAGGER_FRAMES,
            fps,
            config: { damping: 13, mass: 0.6 },
          });
          const opacity = interpolate(enter, [0, 1], [0, 1]);
          const y = interpolate(enter, [0, 1], [70 * s, 0]);
          const scale = interpolate(enter, [0, 1], [0.75, 1]);
          const color = HOOK_LINE_COLORS[i % HOOK_LINE_COLORS.length];
          return (
            <div
              key={i}
              style={{
                opacity,
                transform: `translateY(${y}px) scale(${scale})`,
                fontSize,
                fontWeight: 900,
                color,
                letterSpacing: 3 * s,
                WebkitTextStroke: `${stroke}px ${STROKE_COLOR}`,
                paintOrder: "stroke fill",
                textShadow: `0 ${5 * s}px ${30 * s}px rgba(0,0,0,0.55)`,
                textAlign: "center",
                whiteSpace: "nowrap",
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
