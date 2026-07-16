import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, HOOK_LINE_COLORS, HookOpenerProps, OVERLAY_TEXT_TOP_LANDSCAPE, OVERLAY_TEXT_TOP_PORTRAIT } from "../schema";

// 开屏 5 秒钩子序列（2026-07-15 用户点名，替代冷开场黑卡）：
// 透明背景叠在正片画面上，2~3 条钩子短句自下而上堆叠、逐条 spring 弹入，
// 大字粗黑描边 + 红/黄/白三色轮换——对标博主的首屏钩子打法，首帧即见画面不浪费黄金时间。
const LINE_STAGGER_FRAMES = 24; // 每条短句相隔 0.8s（30fps）依次入场
const STROKE_COLOR = "#141410";

export const HookOpener: React.FC<HookOpenerProps> = ({ lines, offsets, accent }) => {
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
          top: isPortrait ? OVERLAY_TEXT_TOP_PORTRAIT : OVERLAY_TEXT_TOP_LANDSCAPE,
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
          // 弹入时刻：优先用 Python 按口播字符占比估算的 offsets（所见即所听），
          // 缺失时回落固定 0.8s 间隔。
          const enterFrame =
            offsets && offsets[i] !== undefined
              ? Math.round(offsets[i] * fps)
              : i * LINE_STAGGER_FRAMES;
          const enter = spring({
            frame: frame - enterFrame,
            fps,
            config: { damping: 13, mass: 0.6 },
          });
          const opacity = interpolate(enter, [0, 1], [0, 1]);
          const y = interpolate(enter, [0, 1], [70 * s, 0]);
          const scale = interpolate(enter, [0, 1], [0.75, 1]);
          const color = HOOK_LINE_COLORS[i % HOOK_LINE_COLORS.length];
          // 阴影脉冲（借鉴 Remotion 官网"阴影脉冲"）：文字入场后，同色发光的模糊半径按
          // 正弦呼吸，让开屏大字"活"起来更抓眼。用 enter 系数把辉光随弹入渐显，避免文字
          // 还没出现就先发光；~0.8Hz 一次呼吸，节奏舒缓不抢戏。
          const glowPhase = Math.max(0, frame - enterFrame) / fps;
          const glowPulse = 0.5 + 0.5 * Math.sin(glowPhase * Math.PI * 1.6);
          const glow = (10 * s + 20 * s * glowPulse) * Math.min(1, enter * 1.4);
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
                textShadow: `0 0 ${glow}px ${color}, 0 ${5 * s}px ${30 * s}px rgba(0,0,0,0.55)`,
                textAlign: "center",
                whiteSpace: "nowrap",
              }}
            >
              {/* 丨竖线前缀：对标博主的醒目标识，与本行同色更粗一号 */}
              <span style={{ opacity: 0.9, marginRight: 10 * s }}>丨</span>
              {line}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
