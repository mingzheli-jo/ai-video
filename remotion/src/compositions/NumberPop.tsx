import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, NumberPopProps, OVERLAY_TEXT_TOP_LANDSCAPE, OVERLAY_TEXT_TOP_PORTRAIT } from "../schema";

// 数字强调：文案里的关键数字（"3步"“80%”）大字弹出，带过冲回弹，短促淡出。
// 摆在竖屏中上部偏右（避开章节卡的左上与底部字幕）。
// 数字计数（借鉴 Remotion 官网"数字计数"）：数字部分从 0 easeOut 滚到目标值再定格，
// 单位后缀不动；tabular-nums 保证滚动时宽度稳定不抖。
export const NumberPop: React.FC<NumberPopProps> = ({ value, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const isPortrait = height > width;
  const fontSize = 150 * s * (isPortrait ? 1.35 : 1);

  // 前缀数字 + 余下后缀（"5000条" → 5000 / "条"）；不含数字时原样静态展示。
  const numMatch = /^(\d+(?:\.\d+)?)([\s\S]*)$/.exec(value);
  let display = value;
  if (numMatch) {
    const target = parseFloat(numMatch[1]);
    const decimals = (numMatch[1].split(".")[1] || "").length;
    // 前 60% 时长滚数，余下定格展示；三次缓出让收尾稳稳落住。
    const countFrames = Math.max(1, Math.round(durationInFrames * 0.6));
    const p = Math.min(1, frame / countFrames);
    const eased = 1 - Math.pow(1 - p, 3);
    const current = target * eased;
    display = `${decimals ? current.toFixed(decimals) : String(Math.round(current))}${numMatch[2]}`;
  }

  const enter = spring({ frame, fps, config: { damping: 10, mass: 0.5, stiffness: 120 } });
  const scale = interpolate(enter, [0, 1], [0.3, 1]);
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
          right: isPortrait ? "8%" : "12%",
          top: isPortrait ? OVERLAY_TEXT_TOP_PORTRAIT : OVERLAY_TEXT_TOP_LANDSCAPE,
          transform: `scale(${scale})`,
          transformOrigin: "center",
          opacity: Math.min(opacityIn, opacityOut),
          fontSize,
          fontWeight: 900,
          color: accent,
          letterSpacing: 2 * s,
          textShadow: `0 ${8 * s}px ${36 * s}px rgba(0,0,0,0.6), 0 0 ${18 * s}px rgba(0,0,0,0.4)`,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {display}
      </div>
    </AbsoluteFill>
  );
};
