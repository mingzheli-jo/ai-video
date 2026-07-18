import React from "react";
import { AbsoluteFill, Img, useVideoConfig } from "remotion";
import { CoverCardProps, FONT_STACK } from "../schema";

// 封面卡（publish 阶段静帧，2026-07-16 用户定案）：全渠道统一模板——视频首图做底、
// 压暗渐变、双线金框、左下金条 + 大标题（末行金色点睛），右上可选品牌角标。
// 16:9 与 9:16 共用本组件（Cover16x9 / Cover9x16 两个 composition 只差画幅），
// 保证用户主页里所有封面"板正"一致。
const STROKE_COLOR = "#141410";

// 标题拆行：首个标点处断开成 ≤2 行；无标点的超长标题从中间硬切。
const splitTitle = (title: string): string[] => {
  const t = (title || "").trim();
  const parts = t.split(/[，。！？：:,!?\s]+/).filter(Boolean);
  if (parts.length >= 2) {
    return [parts[0], parts.slice(1).join("，")].slice(0, 2);
  }
  if (t.length > 12) {
    const mid = Math.ceil(t.length / 2);
    return [t.slice(0, mid), t.slice(mid)];
  }
  return t ? [t] : [];
};

export const CoverCard: React.FC<CoverCardProps> = ({ title, tag, accent, bg }) => {
  const { width, height } = useVideoConfig();
  const s = Math.min(width, height) / 1080;
  const lines = splitTitle(title);
  const longest = Math.max(...lines.map((l) => l.length), 1);
  // 单行不换行：字号 = min(宽度预算/最长行, 画幅基准上限)。
  const fontSize = Math.min(((width - 124 * s) * 0.98) / longest, 132 * s);
  const stroke = Math.max(3, 6 * s);

  return (
    <AbsoluteFill style={{ background: "#14120e", fontFamily: FONT_STACK }}>
      {bg ? (
        <>
          {/* 2026-07-18 用户实锤：cover 裁切会砍掉人物，抖音封面检测人物不全影响流量。
              改双层：底层模糊铺满补边，前景 contain 完整显示——底图与画幅比例无论
              差多少，人物永远完整。放大 1.15 把模糊边缘推出画外。 */}
          <Img
            src={bg}
            style={{
              position: "absolute", inset: 0, width: "100%", height: "100%",
              objectFit: "cover", filter: "blur(36px) brightness(0.72)",
              transform: "scale(1.15)",
            }}
          />
          <Img
            src={bg}
            style={{
              position: "absolute", inset: 0, width: "100%", height: "100%",
              objectFit: "contain",
            }}
          />
        </>
      ) : null}
      {/* 压暗渐变：底部重、顶部轻，保标题可读又不闷死画面 */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(180deg, rgba(10,9,6,0.30) 0%, rgba(10,9,6,0.12) 42%, rgba(10,9,6,0.85) 100%)",
        }}
      />
      {/* 双线金框：统一模板的"板正"骨架 */}
      <div
        style={{
          position: "absolute",
          inset: 26 * s,
          border: `${Math.max(2, 3 * s)}px solid rgba(232,184,75,0.85)`,
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 40 * s,
          border: `${Math.max(1, 1.5 * s)}px solid rgba(232,184,75,0.35)`,
        }}
      />
      {tag ? (
        <div
          style={{
            position: "absolute",
            top: 62 * s,
            left: 66 * s,
            display: "flex",
            alignItems: "center",
            gap: 12 * s,
            fontSize: 34 * s,
            fontWeight: 700,
            color: "#f5efdd",
            letterSpacing: 4 * s,
            textShadow: `0 ${3 * s}px ${14 * s}px rgba(0,0,0,0.6)`,
          }}
        >
          <div style={{ width: 8 * s, height: 34 * s, background: accent, borderRadius: 3 * s }} />
          {tag}
        </div>
      ) : null}
      <div style={{ position: "absolute", left: 66 * s, right: 66 * s, bottom: 88 * s }}>
        <div
          style={{
            width: 132 * s,
            height: 10 * s,
            background: accent,
            borderRadius: 4 * s,
            marginBottom: 26 * s,
          }}
        />
        {lines.map((line, i) => (
          <div
            key={i}
            style={{
              fontSize,
              fontWeight: 900,
              color: i === lines.length - 1 && lines.length > 1 ? accent : "#ffffff",
              lineHeight: 1.24,
              letterSpacing: 3 * s,
              WebkitTextStroke: `${stroke}px ${STROKE_COLOR}`,
              paintOrder: "stroke fill",
              textShadow: `0 ${6 * s}px ${30 * s}px rgba(0,0,0,0.6)`,
              whiteSpace: "nowrap",
            }}
          >
            {line}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
