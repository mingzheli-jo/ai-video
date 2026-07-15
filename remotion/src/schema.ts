import { z } from "zod";

// 与 Python 侧 effects_manifest_v1 对齐的 props 契约。
// accent 参数化配色，深底金字/白字由组件默认值决定。

export const introSchema = z.object({
  title: z.string(),
  subtitle: z.string().default(""),
  accent: z.string().default("#e8b84b"),
});

export const chapterCardSchema = z.object({
  index: z.number().int().nonnegative().default(1),
  title: z.string(),
  accent: z.string().default("#e8b84b"),
});

export const lowerThirdSchema = z.object({
  text: z.string(),
  accent: z.string().default("#e8b84b"),
});

export const keyPointsSchema = z.object({
  lines: z.array(z.string()).min(1),
  accent: z.string().default("#e8b84b"),
});

export const quoteCardSchema = z.object({
  text: z.string(),
  accent: z.string().default("#e8b84b"),
});

export const numberPopSchema = z.object({
  value: z.string(),
  accent: z.string().default("#e8b84b"),
});

export const keywordPopSchema = z.object({
  keyword: z.string(),
  accent: z.string().default("#e8b84b"),
  // 三色轮换：红 #e74c3c / 黄 #f1c40f / 白 #ffffff，由 Python 侧按全片动效序号写入。
  color: z.string().default("#ffffff"),
});

export const openingCardSchema = z.object({
  title: z.string(),
  points: z.array(z.string()).default([]),
  accent: z.string().default("#e8b84b"),
});

export const goldenCardSchema = z.object({
  text: z.string(),
  accent: z.string().default("#e8b84b"),
});

export type IntroProps = z.infer<typeof introSchema>;
export type ChapterCardProps = z.infer<typeof chapterCardSchema>;
export type LowerThirdProps = z.infer<typeof lowerThirdSchema>;
export type KeyPointsProps = z.infer<typeof keyPointsSchema>;
export type QuoteCardProps = z.infer<typeof quoteCardSchema>;
export type NumberPopProps = z.infer<typeof numberPopSchema>;
export type KeywordPopProps = z.infer<typeof keywordPopSchema>;
export type OpeningCardProps = z.infer<typeof openingCardSchema>;
export type GoldenCardProps = z.infer<typeof goldenCardSchema>;

// 要点行的循环配色（首色由 accent 覆盖）：金、青、玫红、草绿——深底上都够亮。
export const LINE_PALETTE = ["#e8b84b", "#7ec8e3", "#e37e9c", "#9ce37e"];

// 中文系统字体栈，避免打包字体、跨机器稳定渲染。
export const FONT_STACK =
  '"Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Source Han Sans SC", sans-serif';
