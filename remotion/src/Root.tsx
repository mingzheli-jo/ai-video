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
import {
  chapterCardSchema,
  goldenCardSchema,
  hookOpenerSchema,
  introSchema,
  keyPointsSchema,
  keywordPopSchema,
  lowerThirdSchema,
  numberPopSchema,
  openingCardSchema,
  quoteCardSchema,
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
        durationInFrames={84}
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
