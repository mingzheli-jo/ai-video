import React from "react";
import { Composition } from "remotion";
import { Intro } from "./compositions/Intro";
import { ChapterCard } from "./compositions/ChapterCard";
import { LowerThird } from "./compositions/LowerThird";
import { KeyPoints } from "./compositions/KeyPoints";
import { QuoteCard } from "./compositions/QuoteCard";
import { NumberPop } from "./compositions/NumberPop";
import { KeywordPop } from "./compositions/KeywordPop";
import { OpeningCard } from "./compositions/OpeningCard";
import { GoldenCard } from "./compositions/GoldenCard";
import { HookOpener } from "./compositions/HookOpener";
import { HighlightSweep } from "./compositions/HighlightSweep";
import { TypewriterQuote } from "./compositions/TypewriterQuote";
import { AmbientParticles } from "./compositions/AmbientParticles";
import { CoverCard } from "./compositions/CoverCard";
import {
  ambientParticlesSchema,
  chapterCardSchema,
  coverCardSchema,
  goldenCardSchema,
  highlightSweepSchema,
  hookOpenerSchema,
  introSchema,
  keyPointsSchema,
  keywordPopSchema,
  lowerThirdSchema,
  numberPopSchema,
  openingCardSchema,
  quoteCardSchema,
  typewriterQuoteSchema,
} from "./schema";

// FPS/WIDTH/HEIGHT 仅为 Remotion Studio 预览的默认值：实际渲染时尺寸由 CLI
// --width/--height 覆盖（Python 侧从底片探测，支持 1080x1920 竖屏等任意画幅），
// 组件内部一律用 useVideoConfig() 读取覆盖后的真实尺寸做等比缩放。
// 但 durationInFrames 不是预览默认值——它是渲染帧数硬上限（CLI --frames 越过即失败），
// 必须 ≥ Python 侧该 composition 的最大可能时长，并与 effects.py 的
// _MAX_FRAMES_BY_COMPOSITION 逐条同源（有测试盯守）。2026-07-16 金句卡 84 帧、
// 2026-07-21 HookOpener 150 帧两次事故都栽在把它当预览默认值改小/没跟上。
const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Intro"
        component={Intro}
        durationInFrames={75}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={introSchema}
        defaultProps={{
          title: "示例片头",
          subtitle: "副标题",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="ChapterCard"
        component={ChapterCard}
        durationInFrames={45}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={chapterCardSchema}
        defaultProps={{
          index: 1,
          title: "章节标题",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="LowerThird"
        component={LowerThird}
        durationInFrames={120}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={lowerThirdSchema}
        defaultProps={{
          text: "字幕条文字",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="KeyPoints"
        component={KeyPoints}
        durationInFrames={100}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={keyPointsSchema}
        defaultProps={{
          lines: ["要点一", "要点二", "要点三"],
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="QuoteCard"
        component={QuoteCard}
        // 契约：durationInFrames 是渲染帧数上限（CLI --frames 不能越过它）。
        // 真实上限不是 QUOTE_DURATION——那是表达式里的**下限**。实际时长是
        // min(max(span+1.2, QUOTE_DURATION), TYPEWRITER_MAX_DURATION+1.5) = 最大 6.0s
        // ×30 = 180 帧（2026-07-21 实测 studio_0721_213852 出现 5.60s/168 帧被钳制）。
        durationInFrames={180}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={quoteCardSchema}
        defaultProps={{
          text: "示例金句",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="NumberPop"
        component={NumberPop}
        durationInFrames={42}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={numberPopSchema}
        defaultProps={{
          value: "80%",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="KeywordPop"
        component={KeywordPop}
        durationInFrames={48}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={keywordPopSchema}
        defaultProps={{
          keyword: "关键词",
          accent: "#e8b84b",
          color: "#ffffff",
        }}
      />
      <Composition
        id="OpeningCard"
        component={OpeningCard}
        durationInFrames={36}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={openingCardSchema}
        defaultProps={{
          title: "主题词",
          points: ["要点一", "要点二"],
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="GoldenCard"
        component={GoldenCard}
        durationInFrames={72}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={goldenCardSchema}
        defaultProps={{
          text: "核心金句示例",
          accent: "#e8b84b",
        }}
      />
      {/* 发布封面（publish 阶段 remotion still 静帧）：两个画幅共用 CoverCard 组件 */}
      <Composition
        id="Cover16x9"
        component={CoverCard}
        durationInFrames={1}
        fps={FPS}
        width={1920}
        height={1080}
        schema={coverCardSchema}
        defaultProps={{ title: "示例封面标题，统一模板", tag: "", accent: "#e8b84b", bg: "" }}
      />
      <Composition
        id="Cover9x16"
        component={CoverCard}
        durationInFrames={1}
        fps={FPS}
        width={1080}
        height={1920}
        schema={coverCardSchema}
        defaultProps={{ title: "示例封面标题，统一模板", tag: "", accent: "#e8b84b", bg: "" }}
      />
      {/* 抖音横版封面（2026-07-21 用户实测）：抖音横版投稿的封面选择框按 4:3 收，
          直接传 16:9 会被要求手动裁掉左右。1440x1080 = 4:3，与成片同高、免裁。
          注意与下面的 Cover3x4 方向相反：4:3 是横版封面本身，3:4 是竖版主页展示位。 */}
      <Composition
        id="Cover4x3"
        component={CoverCard}
        durationInFrames={1}
        fps={FPS}
        width={1440}
        height={1080}
        schema={coverCardSchema}
        defaultProps={{ title: "示例封面标题，统一模板", tag: "", accent: "#e8b84b", bg: "" }}
      />
      {/* 抖音横版视频的主页展示位封面（2026-07-16 实测：抖音主页作品位是竖向
          ~1080x1464，16:9 封面会被中心裁掉左右——传横版视频时选这张 3:4，主页展示才完整） */}
      <Composition
        id="Cover3x4"
        component={CoverCard}
        durationInFrames={1}
        fps={FPS}
        width={1080}
        height={1464}
        schema={coverCardSchema}
        defaultProps={{ title: "示例封面标题，统一模板", tag: "", accent: "#e8b84b", bg: "" }}
      />
      <Composition
        id="AmbientParticles"
        component={AmbientParticles}
        durationInFrames={240}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={ambientParticlesSchema}
        defaultProps={{ accent: "#e8b84b" }}
      />
      <Composition
        id="HighlightSweep"
        component={HighlightSweep}
        // 实际时长 min(max(span+0.4, HIGHLIGHT_SWEEP_DURATION), 3.2) = 最大 3.2s×30
        // = 96 帧（2026-07-21 实测 studio_0721_213852 两条 3.20s 被钳制到 60）。
        durationInFrames={96}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={highlightSweepSchema}
        defaultProps={{
          text: "真正困住你的，是心中之贼",
          keyword: "心中之贼",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="TypewriterQuote"
        component={TypewriterQuote}
        // 与 QuoteCard 由**同一个表达式**派生时长（effects.py 同一处 duration 变量
        // 按正则决定出 typewriter_quote 还是 quote_card），上限必须一致取 180。
        durationInFrames={180}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={typewriterQuoteSchema}
        defaultProps={{
          source: "王阳明",
          text: "此心不动，随机而动。",
          accent: "#e8b84b",
        }}
      />
      <Composition
        id="HookOpener"
        component={HookOpener}
        // 契约：上限必须 ≥ Python 侧最大可能时长。HookOpener 被两类特效复用：
        // hook_opener（HOOK_OPENER_MAX_DURATION 5s）和 golden_lines 多句同步时刻
        // （SYNC_MOMENT_MAX_DURATION 7s×30=210）。2026-07-21 审查实锤：旧上限 150
        // 让跨度 >3.8s 的多句时刻渲染必败且被吞成 warning——复用组件时上限没跟上。
        durationInFrames={210}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
        schema={hookOpenerSchema}
        defaultProps={{
          lines: ["钩子短句一", "钩子短句二", "钩子短句三"],
          offsets: [0, 0.8, 1.6],
          accent: "#e8b84b",
        }}
      />
    </>
  );
};
