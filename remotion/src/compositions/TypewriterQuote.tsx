import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FONT_STACK, TypewriterQuoteProps } from "../schema";

// 打字机引用（借鉴 Remotion 官网"打字机效果"，2026-07-16 用户拍板）：带出处的原典
// 引用（"王阳明：此心不动，随机而动"）正文逐字浮现，有"书写"氛围；打完后出处行
// 淡入落款。只在引用文本形如「出处：正文」时由派生侧选用（见 effects），
// 普通金句仍走 QuoteCard，避免打字机滥用拖节奏。
export const TypewriterQuote: React.FC<TypewriterQuoteProps> = ({ source, text, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const s = width / 1920;
  const isPortrait = height > width;
  const fontSize = 58 * s * (isPortrait ? 1.3 : 1);

  // 前 65% 时长逐字打完，余下定格给观众读；光标恒速闪烁。
  const typeFrames = Math.max(1, Math.round(durationInFrames * 0.65));
  const typedCount = Math.min(
    text.length,
    Math.floor(interpolate(frame, [0, typeFrames], [0, text.length], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    })),
  );
  const typed = text.slice(0, typedCount);
  const cursorOn = Math.floor(frame / (fps * 0.4)) % 2 === 0;

  // 出处落款：正文打完后 0.2s 淡入。
  const doneFrame = typeFrames + Math.round(fps * 0.2);
  const sourceOpacity = interpolate(frame, [doneFrame, doneFrame + 8], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const opacityIn = interpolate(frame, [0, 6], [0, 1], { extrapolateRight: "clamp" });
  const outStart = durationInFrames - 8;
  const opacityOut = interpolate(frame, [outStart, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        fontFamily: FONT_STACK,
        justifyContent: "center",
        alignItems: "center",
        opacity: Math.min(opacityIn, opacityOut),
      }}
    >
      <div style={{ position: "absolute", inset: 0, background: "rgba(8,8,12,0.62)" }} />
      <div
        style={{
          position: "relative",
          maxWidth: isPortrait ? width * 0.84 : width * 0.66,
          textAlign: "center",
        }}
      >
        <div
          style={{
            fontSize,
            fontWeight: 800,
            color: "#f5efdd",
            lineHeight: 1.6,
            letterSpacing: 3 * s,
            textShadow: `0 ${6 * s}px ${30 * s}px rgba(0,0,0,0.65)`,
            minHeight: fontSize * 1.6,
          }}
        >
          {typed}
          <span
            style={{
              display: "inline-block",
              width: 3 * s,
              height: fontSize * 0.9,
              background: accent,
              verticalAlign: `-${fontSize * 0.12}px`,
              marginLeft: 4 * s,
              opacity: cursorOn ? 1 : 0,
            }}
          />
        </div>
        <div
          style={{
            marginTop: 20 * s,
            fontSize: fontSize * 0.44,
            fontWeight: 700,
            color: accent,
            letterSpacing: 6 * s,
            opacity: sourceOpacity,
          }}
        >
          —— {source}
        </div>
      </div>
    </AbsoluteFill>
  );
};
