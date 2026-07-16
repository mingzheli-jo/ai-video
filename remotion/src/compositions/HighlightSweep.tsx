import React from "react";
import {
  AbsoluteFill,
  interpolate,
  interpolateColors,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  FONT_STACK,
  HighlightSweepProps,
  OVERLAY_TEXT_TOP_LANDSCAPE,
  OVERLAY_TEXT_TOP_PORTRAIT,
} from "../schema";

// 荧光笔高亮（借鉴 Remotion 官网"荧光笔高亮"，2026-07-16 用户拍板）：讲到关键词的
// 瞬间，含它的句子上下文浮现，一笔金色从左向右扫过关键词、字色随之压深——"划重点"
// 的隐喻。与 keyword_pop 互补：弹出是"喊"，高亮是"点"；派生侧在命中主时间轴的
// 关键词里隔条轮替两种形态（见 effects._build_rich_effects）。
const STROKE_COLOR = "#141410";
const INK_COLOR = "#1a160c"; // 扫过后关键词压深的墨色（金底上够黑）

export const HighlightSweep: React.FC<HighlightSweepProps> = ({ text, keyword, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const isPortrait = height > width;
  // 上下文超 14 字整体收字号，保证单行放得下不换行。
  const shrink = text.length > 14 ? Math.max(0.7, 14 / text.length) : 1;
  const fontSize = 68 * s * (isPortrait ? 1.3 : 1) * shrink;
  const stroke = Math.max(2, 4 * s);

  // 行入场：轻微上移淡入（比弹出克制——主角是随后那一笔高亮）。
  const enter = spring({ frame, fps, config: { damping: 15, mass: 0.5 } });
  const lineOpacity = interpolate(enter, [0, 1], [0, 1]);
  const lineY = interpolate(enter, [0, 1], [30 * s, 0]);

  // 高亮扫过：行入场 0.25s 后起笔，0.5s 扫完；字色在扫过中后段压深。
  const sweepStart = Math.round(fps * 0.25);
  const sweepFrames = Math.max(1, Math.round(fps * 0.5));
  const sweepRaw = interpolate(frame, [sweepStart, sweepStart + sweepFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const sweep = 1 - Math.pow(1 - sweepRaw, 3);
  const ink = interpolate(sweep, [0.35, 1], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const kwColor = interpolateColors(ink, [0, 1], ["#ffffff", INK_COLOR]);

  const outStart = durationInFrames - 8;
  const opacityOut = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // 按首次出现位置拆 前文 | 关键词 | 后文；找不到（防御）就把整行当关键词扫。
  const at = text.indexOf(keyword);
  const before = at >= 0 ? text.slice(0, at) : "";
  const kw = at >= 0 ? keyword : text;
  const after = at >= 0 ? text.slice(at + keyword.length) : "";

  return (
    <AbsoluteFill style={{ fontFamily: FONT_STACK }}>
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: isPortrait ? OVERLAY_TEXT_TOP_PORTRAIT : OVERLAY_TEXT_TOP_LANDSCAPE,
          transform: `translateX(-50%) translateY(${lineY}px)`,
          opacity: Math.min(lineOpacity, opacityOut),
          fontSize,
          fontWeight: 900,
          color: "#ffffff",
          letterSpacing: 2 * s,
          WebkitTextStroke: `${stroke}px ${STROKE_COLOR}`,
          paintOrder: "stroke fill",
          textShadow: `0 ${5 * s}px ${26 * s}px rgba(0,0,0,0.55)`,
          whiteSpace: "nowrap",
        }}
      >
        {before}
        <span style={{ position: "relative", display: "inline-block", padding: `0 ${6 * s}px` }}>
          <span
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              bottom: fontSize * 0.06,
              height: fontSize * 0.48,
              background: accent,
              borderRadius: 4 * s,
              transform: `scaleX(${sweep})`,
              transformOrigin: "left center",
              boxShadow: `0 ${3 * s}px ${14 * s}px rgba(0,0,0,0.35)`,
            }}
          />
          <span
            style={{
              position: "relative",
              color: kwColor,
              // 扫过后墨色字不再需要描边撑对比，随 ink 淡出描边避免脏边。
              WebkitTextStroke: `${stroke * (1 - ink)}px ${STROKE_COLOR}`,
              paintOrder: "stroke fill",
            }}
          >
            {kw}
          </span>
        </span>
        {after}
      </div>
    </AbsoluteFill>
  );
};
