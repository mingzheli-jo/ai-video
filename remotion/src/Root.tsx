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

// 以下 FPS/WIDTH/HEIGHT/durationInFrames 仅为 Remotion Studio 预览的默认值。
// 实际渲染时：尺寸由 CLI --width/--height 覆盖（Python 侧从底片探测，支持 1080x1920
// 竖屏等任意画幅），时长由 CLI --frames / manifest 的 duration 派生。组件内部一律用
// useVideoConfig() 读取覆盖后的真实尺寸做等比缩放，不依赖这里的默认值。
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
        // 必须 ≥ Python 侧最大可能时长（QUOTE_DURATION 4.5s×30=135），留余量取 150。
        // 2026-07-16 事故：4.5s 金句卡在旧上限 84 帧下渲染直接失败。
        durationInFrames={150}
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
      {/* 抖音横版视频专用封面（2026-07-16 实测：抖音主页作品位是竖向 ~1080x1464，
          16:9 封面会被中心裁掉左右——上传横版视频时选这张 3:4，主页展示才完整） */}
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
        durationInFrames={60}
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
        // 同 QuoteCard 的契约注释：上限必须 ≥ TYPEWRITER_MAX_DURATION 4.5s×30=135。
        durationInFrames={150}
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
        durationInFrames={150}
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
