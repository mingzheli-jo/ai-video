from __future__ import annotations

import argparse
import cgi
import json
import math
import mimetypes
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote, urlparse

from video_factory.original import render_original_video
from video_factory.originality import build_originality_report
from video_factory.reference_guided_original import render_reference_guided_original_video
from video_factory.replicate import DEFAULT_OUTPUT_ROOT, render_replicate
from video_factory.source_download import SourceDownloadError, download_source_video, parse_source_urls


WORKBENCH_ROOT = DEFAULT_OUTPUT_ROOT / "workbench"
UPLOAD_ROOT = WORKBENCH_ROOT / "uploads"
WORKFLOW_CHOICES = {"replicate", "original", "reference_guided_original"}
MODE_CHOICES = {
    "auto",
    "original-enhanced",
    "human-edit",
    "creative-edit",
    "original-generate",
    "reference-guided-original",
}
DEFAULT_PRESET_ID = "tutorial_longform"
PRODUCTION_PRESETS = {
    "foolproof_original": {
        "label": "一键原创视频",
        "mode": "reference-guided-original",
        "target_duration_policy": "source_guided",
        "quality_strictness": "audit",
        "creative_strength": "strong",
        "audio_policy": "replace_later",
        "visual_asset_strategy": "images2_first",
        "original_insert_policy": "chapter_explainers",
    },
    "original_tutorial": {
        "label": "无参考原创（高级）",
        "mode": "original-generate",
        "target_duration_policy": "source_guided",
        "quality_strictness": "audit",
        "creative_strength": "strong",
        "audio_policy": "replace_later",
        "visual_asset_strategy": "images2_first",
        "original_insert_policy": "chapter_explainers",
    },
    "tutorial_longform": {
        "label": "发布增强",
        "mode": "creative-edit",
        "target_duration_policy": "source_guided",
        "quality_strictness": "audit",
        "creative_strength": "strong",
        "audio_policy": "normalize_only",
        "visual_asset_strategy": "images2_contextual_inserts",
        "original_insert_policy": "chapter_explainers",
    },
    "human_edit": {
        "label": "真人剪辑",
        "mode": "human-edit",
        "target_duration_policy": "retain_core",
        "quality_strictness": "standard",
        "creative_strength": "light",
        "audio_policy": "preserve_source",
        "visual_asset_strategy": "user_owned_first",
        "original_insert_policy": "none",
    },
    "original_enhanced": {
        "label": "原片增强",
        "mode": "original-enhanced",
        "target_duration_policy": "keep_original",
        "quality_strictness": "standard",
        "creative_strength": "light",
        "audio_policy": "preserve_source",
        "visual_asset_strategy": "user_owned_first",
        "original_insert_policy": "none",
    },
    "food_real_cut": {
        "label": "美食真实剪辑",
        "mode": "creative-edit",
        "target_duration_policy": "source_guided",
        "quality_strictness": "audit",
        "creative_strength": "strong",
        "audio_policy": "replace_later",
        "visual_asset_strategy": "images2_first",
        "original_insert_policy": "chapter_explainers",
    },
}
QUALITY_STRICTNESS_CHOICES = {"standard", "strict", "audit"}
CREATIVE_STRENGTH_CHOICES = {"light", "balanced", "strong"}
TARGET_DURATION_POLICIES = {"source_guided", "retain_core", "keep_original", "short_summary"}
AUDIO_POLICY_CHOICES = {"preserve_source", "normalize_only", "replace_later"}
VISUAL_TRANSFORM_POLICY_CHOICES = {"none", "remove_presenter", "face_only"}
VISUAL_ASSET_STRATEGY_CHOICES = {
    "images2_contextual_inserts",
    "images2_first",
    "images2_only",
    "licensed_stock_fallback",
    "user_owned_first",
}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>视频发布增强工作台</title>
  <style>
    :root {
      --paper: #eef0ec;
      --ink: #151719;
      --muted: #5d6768;
      --line: #c8d0c9;
      --panel: #fbfcf8;
      --green: #17845f;
      --blue: #255f83;
      --amber: #a86c1d;
      --red: #b4443f;
      --slate: #20262b;
      --shadow: 0 18px 50px rgba(17, 22, 24, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(23, 26, 31, 0.035) 1px, transparent 1px),
        linear-gradient(rgba(23, 26, 31, 0.035) 1px, transparent 1px),
        var(--paper);
      background-size: 28px 28px;
      font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }
    main {
      width: min(1180px, calc(100vw - 40px));
      margin: 0 auto;
      padding: 34px 0 46px;
    }
    header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: clamp(30px, 4vw, 58px);
      line-height: 0.95;
      font-weight: 760;
      max-width: 720px;
    }
    .subtitle {
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.7;
      max-width: 720px;
    }
    .status-pill {
      min-width: 154px;
      border: 1px solid var(--line);
      background: rgba(251, 252, 248, 0.86);
      padding: 10px 14px;
      text-align: center;
      font-size: 13px;
      font-weight: 700;
      box-shadow: var(--shadow);
    }
    .capability-rail {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 8px;
    }
    .rail-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 14px;
      margin-top: 18px;
    }
    .rail-head strong {
      color: var(--green);
      font-size: 13px;
      font-weight: 840;
    }
    .rail-head span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      text-align: right;
    }
    .capability-item {
      min-height: 92px;
      border: 1px solid var(--line);
      background: rgba(251, 252, 248, 0.92);
      padding: 12px;
      box-shadow: var(--shadow);
    }
    .capability-item::before {
      content: "";
      display: block;
      width: 30px;
      height: 3px;
      margin-bottom: 10px;
      background: var(--green);
    }
    .capability-item strong {
      display: block;
      font-size: 13px;
      line-height: 1.25;
    }
    .capability-item span {
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(340px, 0.92fr) minmax(420px, 1.08fr);
      gap: 18px;
      margin-top: 22px;
    }
    .panel {
      border: 1px solid var(--line);
      background: rgba(251, 252, 248, 0.94);
      box-shadow: var(--shadow);
      padding: 20px;
    }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 17px;
      letter-spacing: 0;
    }
    label {
      display: block;
      margin: 16px 0 7px;
      color: #303733;
      font-size: 13px;
      font-weight: 720;
    }
    input[type="text"],
    textarea,
    select {
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 13px 12px;
      font-size: 14px;
      outline: none;
      font-family: inherit;
    }
    textarea {
      min-height: 96px;
      resize: vertical;
      line-height: 1.55;
    }
    select {
      appearance: none;
    }
    input[type="text"]:focus,
    textarea:focus,
    select:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(44, 111, 187, 0.14);
    }
    .drop-zone {
      border: 1.5px dashed #9c9487;
      background: #f4f6f1;
      padding: 24px 16px;
      min-height: 132px;
      display: grid;
      place-items: center;
      text-align: center;
      cursor: pointer;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }
    .drop-zone:hover,
    .drop-zone.dragover {
      border-color: var(--green);
      background: #eaf5ee;
      transform: translateY(-1px);
    }
    .drop-title {
      font-weight: 760;
      font-size: 15px;
    }
    .drop-note {
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    #fileInput {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      opacity: 0;
    }
    .mode-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    .workflow-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }
    .workflow-grid input {
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }
    .workflow-grid label {
      margin: 0;
      min-height: 86px;
      border: 1px solid var(--line);
      background: #fff;
      padding: 14px;
      cursor: pointer;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }
    .workflow-grid strong {
      display: block;
      font-size: 15px;
      margin-bottom: 6px;
    }
    .workflow-grid span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 500;
    }
    .workflow-grid input:checked + label {
      border-color: var(--green);
      background: #ecf7f0;
      transform: translateY(-1px);
    }
    .source-section.is-hidden,
    .original-section.is-hidden,
    .replicate-only.is-hidden {
      display: none;
    }
    .local-source-details {
      margin-top: 14px;
      border: 1px solid var(--line);
      background: #f7faf6;
    }
    .local-source-details summary {
      cursor: pointer;
      padding: 11px 12px;
      color: var(--green);
      font-size: 13px;
      font-weight: 820;
      list-style-position: inside;
    }
    .local-source-inner {
      border-top: 1px solid var(--line);
      padding: 14px;
      background: rgba(251, 252, 248, 0.78);
    }
    .mode-grid input {
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }
    .mode-grid label {
      margin: 0;
      min-height: 76px;
      border: 1px solid var(--line);
      background: #fff;
      padding: 12px;
      cursor: pointer;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }
    .mode-grid strong {
      display: block;
      font-size: 13px;
      margin-bottom: 5px;
    }
    .mode-grid span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 500;
    }
    .mode-grid input:checked + label {
      border-color: var(--green);
      background: #ecf7f0;
      transform: translateY(-1px);
    }
    .mode-inspector {
      margin-top: 12px;
      border: 1px solid var(--line);
      background: #eef5f1;
      padding: 12px;
    }
    .mode-inspector-title {
      color: var(--green);
      font-size: 12px;
      font-weight: 820;
      text-transform: uppercase;
    }
    .mode-inspector-body {
      margin-top: 7px;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.55;
      font-weight: 650;
    }
    .mode-chip-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }
    .mode-chip {
      border: 1px solid rgba(23, 132, 95, 0.28);
      background: #fbfcf8;
      color: #24423a;
      padding: 5px 7px;
      font-size: 11px;
      font-weight: 760;
    }
    .production-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    .advanced-options {
      margin-top: 14px;
      border: 1px solid var(--line);
      background: #f7faf6;
      padding: 0;
    }
    .advanced-options summary {
      cursor: pointer;
      padding: 12px 13px;
      color: var(--green);
      font-size: 13px;
      font-weight: 820;
      list-style-position: inside;
    }
    .advanced-options-inner {
      border-top: 1px solid var(--line);
      padding: 0 13px 13px;
      background: rgba(251, 252, 248, 0.78);
    }
    .production-field label {
      margin-top: 0;
    }
    .production-field.full {
      grid-column: 1 / -1;
    }
    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 14px;
      margin-bottom: 14px;
      padding: 10px;
      border: 1px solid var(--line);
      background: rgba(251, 252, 248, 0.96);
      position: sticky;
      top: 10px;
      z-index: 8;
      box-shadow: 0 12px 34px rgba(17, 22, 24, 0.10);
    }
    button {
      border: 0;
      background: var(--ink);
      color: #fff;
      padding: 13px 18px;
      min-height: 46px;
      font-size: 14px;
      font-weight: 760;
      cursor: pointer;
    }
    button:disabled {
      cursor: wait;
      opacity: 0.56;
    }
    .secondary {
      background: transparent;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
    }
    .job-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .job-state {
      color: #fff;
      background: var(--muted);
      padding: 6px 9px;
      font-size: 12px;
      font-weight: 760;
      min-width: 74px;
      text-align: center;
    }
    .job-state.running { background: var(--blue); }
    .job-state.done { background: var(--green); }
    .job-state.error { background: var(--red); }
    .summary-board {
      border: 1px solid var(--line);
      background: #f7faf6;
      padding: 14px;
      margin-bottom: 12px;
    }
    .summary-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
      cursor: pointer;
      list-style: none;
    }
    .summary-top::-webkit-details-marker {
      display: none;
    }
    .summary-top::before {
      content: "＋";
      color: var(--ink);
      font-size: 14px;
      font-weight: 900;
    }
    .summary-board[open] .summary-top::before {
      content: "－";
    }
    .summary-board:not([open]) .summary-top {
      margin-bottom: 0;
    }
    .summary-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .summary-kicker {
      color: var(--green);
      font-size: 12px;
      font-weight: 820;
      text-transform: uppercase;
    }
    .summary-status {
      min-width: 82px;
      padding: 5px 8px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      text-align: center;
      font-size: 12px;
      font-weight: 800;
    }
    .summary-status.pass {
      border-color: rgba(23, 132, 95, 0.36);
      color: var(--green);
      background: #edf8f1;
    }
    .summary-status.fail {
      border-color: rgba(180, 68, 63, 0.36);
      color: var(--red);
      background: #fff1ef;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .metric {
      min-height: 62px;
      border: 1px solid var(--line);
      background: #fff;
      padding: 9px;
      overflow: hidden;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 720;
    }
    .metric strong {
      display: block;
      margin-top: 5px;
      font-size: 13px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }
    .check-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px;
      margin-top: 10px;
    }
    .check-pill {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 34px;
      border: 1px solid var(--line);
      background: #fff;
      padding: 7px 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    .check-pill.pass {
      border-color: rgba(23, 132, 95, 0.32);
      background: #edf8f1;
      color: #225544;
    }
    .check-pill.fail {
      border-color: rgba(180, 68, 63, 0.32);
      background: #fff1ef;
      color: #82332d;
    }
    .check-mark {
      flex: 0 0 auto;
      font-size: 11px;
      font-weight: 900;
    }
    .issue-list {
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .issue-list strong {
      color: var(--red);
    }
    .queue-board,
    .history-board {
      border: 1px solid var(--line);
      background: #fff;
      padding: 12px;
      margin-bottom: 12px;
    }
    .board-title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 8px;
      color: var(--green);
      font-size: 12px;
      font-weight: 840;
    }
    .queue-list,
    .history-list {
      display: grid;
      gap: 7px;
      max-height: 168px;
      overflow: auto;
    }
    .queue-item,
    .history-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      border: 1px solid var(--line);
      padding: 8px;
      background: #fbfcf8;
      font-size: 12px;
      line-height: 1.4;
    }
    .history-item {
      cursor: pointer;
    }
    .history-item:hover {
      border-color: var(--blue);
    }
    .queue-item strong,
    .history-item strong {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .mini-state {
      align-self: start;
      border: 1px solid var(--line);
      padding: 3px 6px;
      color: var(--muted);
      font-weight: 820;
    }
    .mini-state.done { color: var(--green); border-color: rgba(23, 132, 95, 0.32); }
    .mini-state.error { color: var(--red); border-color: rgba(180, 68, 63, 0.32); }
    .repair-action {
      min-height: 32px;
      padding: 6px 9px;
      font-size: 12px;
    }
    .log {
      min-height: 190px;
      max-height: 260px;
      overflow: auto;
      border: 1px solid var(--line);
      background: var(--slate);
      color: #f4f1e7;
      padding: 14px;
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 12px;
      line-height: 1.65;
      white-space: pre-wrap;
    }
    .artifacts {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 9px;
      padding: 10px;
      border-top: 1px solid var(--line);
      background: rgba(251, 252, 248, 0.74);
    }
    .artifact-details {
      margin-top: 14px;
      border: 1px solid var(--line);
      background: #f7faf6;
    }
    .artifact-details summary {
      min-height: 42px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      color: var(--green);
      font-size: 13px;
      font-weight: 820;
      list-style: none;
    }
    .artifact-details summary::-webkit-details-marker {
      display: none;
    }
    .artifact-details summary::before {
      content: "＋";
      color: var(--ink);
      font-weight: 900;
    }
    .artifact-details[open] summary::before {
      content: "－";
    }
    .artifact-summary-title {
      flex: 1 1 auto;
    }
    .artifact-count {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    .artifact {
      display: block;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 11px 12px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 720;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .artifact:hover {
      border-color: var(--blue);
      color: var(--blue);
    }
    .preview {
      margin-top: 14px;
      border: 1px solid var(--line);
      background: #fff;
      min-height: 180px;
      display: grid;
      place-items: center;
      overflow: hidden;
    }
    .preview-shell {
      width: 100%;
    }
    .preview-tools {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      padding: 8px;
      border-bottom: 1px solid var(--line);
      background: #fbf7ef;
    }
    .preview-action,
    .preview-tools a {
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 7px 10px;
      font-size: 12px;
      font-weight: 760;
      text-decoration: none;
      cursor: pointer;
    }
    .preview-action:hover,
    .preview-tools a:hover {
      border-color: var(--blue);
      color: var(--blue);
    }
    .preview video,
    .preview img {
      width: 100%;
      max-height: 320px;
      object-fit: contain;
      background: #111;
    }
    .path-panel {
      margin-top: 10px;
      border: 1px solid var(--line);
      background: #fbfcf8;
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .path-row {
      display: grid;
      grid-template-columns: 96px minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-height: 38px;
    }
    .path-row.no-copy {
      grid-template-columns: 96px minmax(0, 1fr);
    }
    .path-row span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 820;
    }
    .path-row code {
      display: block;
      border: 1px solid var(--line);
      background: #fff;
      padding: 8px 9px;
      color: var(--ink);
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      font-size: 11px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .copy-path {
      min-height: 34px;
      padding: 7px 10px;
      font-size: 12px;
    }
    .title-candidates {
      grid-column: 1 / -1;
      display: grid;
      gap: 7px;
      margin-top: 2px;
    }
    .title-candidate {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      padding: 8px 10px;
      text-align: left;
      font-size: 12px;
      line-height: 1.35;
      font-weight: 760;
    }
    .title-candidate:hover {
      border-color: var(--blue);
      color: var(--blue);
    }
    .preview-modal {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: none;
      grid-template-rows: auto minmax(0, 1fr);
      background: rgba(15, 17, 20, 0.94);
      color: #fff;
    }
    .preview-modal.open {
      display: grid;
    }
    .preview-modal-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.16);
    }
    .preview-modal-bar strong {
      font-size: 14px;
    }
    .preview-modal-bar button {
      min-height: 36px;
      background: #fff;
      color: var(--ink);
      padding: 8px 12px;
    }
    .preview-modal-body {
      min-height: 0;
      display: grid;
      place-items: center;
      padding: 18px;
    }
    .preview-modal-body video,
    .preview-modal-body img {
      width: min(100%, 1280px);
      max-height: calc(100vh - 88px);
      object-fit: contain;
      background: #000;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.36);
    }
    @media (max-width: 880px) {
      main { width: min(100vw - 24px, 680px); }
      header { display: block; }
      .status-pill { margin-top: 16px; }
      .rail-head { display: block; }
      .rail-head span { display: block; margin-top: 4px; text-align: left; }
      .capability-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
      .workflow-grid { grid-template-columns: 1fr; }
      .mode-grid { grid-template-columns: 1fr; }
      .production-grid { grid-template-columns: 1fr; }
      .artifacts { grid-template-columns: 1fr; }
      .metric-grid,
      .check-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>视频发布增强工作台</h1>
        <p class="subtitle">默认只做一件事：围绕你提供的自有或授权视频做修复增强、重排混剪、封面、字幕和发布质检。系统会先分析原片，再决定是否用 images2 生成少量相关补充镜头。</p>
      </div>
      <div class="status-pill" id="serverStatus">LOCAL READY</div>
    </header>

    <section class="layout">
      <form class="panel" id="jobForm">
        <h2>视频发布增强</h2>
        <p class="hint">给视频链接、本地路径或上传文件即可。默认保留原片价值，补齐画质、比例、字幕、封面、节奏和发布前质检。</p>

        <div class="source-section" id="sourceSection">
          <label for="sourceUrls">视频链接</label>
          <textarea id="sourceUrls" name="source_urls" placeholder="https://v.douyin.com/xxxx&#10;https://www.youtube.com/watch?v=xxxx"></textarea>
          <p class="hint">支持抖音和 YouTube 公开视频，一行一个链接。系统会先下载到当前任务目录，再进入发布增强流程；不会读取登录态、Cookie、私密视频或受限内容。</p>

          <details class="local-source-details" id="localSourceDetails">
            <summary>本地文件和路径</summary>
            <div class="local-source-inner">
              <div class="drop-zone" id="dropZone">
                <input id="fileInput" name="file" type="file" accept="video/*" multiple>
                <div>
                  <div class="drop-title" id="fileLabel">拖入一批视频，或点击选择</div>
                  <div class="drop-note">也可以在下面逐行填写本机视频绝对路径</div>
                </div>
              </div>

              <label for="inputPath">批量路径</label>
              <textarea id="inputPath" name="input_path" placeholder="/Users/king/Downloads/example-a.mp4&#10;/Users/king/Downloads/example-b.mp4"></textarea>
            </div>
          </details>
        </div>

        <div class="original-section" id="originalSection">
          <label for="originalTopic">发布目标（可选）</label>
          <input id="originalTopic" name="original_topic" type="text" placeholder="不填也可以；例如：适合抖音发布、保留教程完整感、突出关键步骤">

          <label for="originalBrief">补充要求（可选）</label>
          <textarea id="originalBrief" name="original_brief" placeholder="不填则自动判断；也可以写目标观众、重点、不要出现的内容、素材限制和发布平台。"></textarea>
        </div>

        <div class="production-field full front-visual-field">
          <label for="visualAssetStrategy">AI 补充镜头策略</label>
          <select id="visualAssetStrategy" name="visual_asset_strategy">
            <option value="images2_contextual_inserts" selected>images2 按需补充</option>
            <option value="user_owned_first">自有素材优先</option>
            <option value="licensed_stock_fallback">授权素材补位</option>
            <option value="images2_first">images2 补图</option>
            <option value="images2_only">只用 AI 补图（实验）</option>
          </select>
          <p class="hint">先分析原片，再用 images2 生成封面、解释镜头、对比画面或过渡画面；AI 画面只做辅助，不能替代真实原片。</p>
        </div>

        <div class="production-field full front-duration-field">
          <label for="durationPolicy">目标时长</label>
          <select id="durationPolicy" name="target_duration_policy">
            <option value="source_guided">接近原片</option>
            <option value="retain_core">保留主线</option>
            <option value="short_summary">短摘要</option>
          </select>
        </div>

        <div class="actions">
          <button id="startButton" type="submit">开始发布增强</button>
          <button class="secondary" id="clearButton" type="button">清空</button>
        </div>

        <details class="advanced-options" id="advancedOptions">
          <summary>专家设置</summary>
          <div class="advanced-options-inner">
            <label>功能入口</label>
            <div class="workflow-grid">
              <input id="workflow-replicate" type="radio" name="workflow" value="replicate" checked>
              <label for="workflow-replicate"><strong>视频修复 / 增强</strong><span>处理自有或授权视频，做剪辑增强、字幕、封面、画质和相似度风控。</span></label>
            </div>
            <input id="workflow-reference-guided-original" type="radio" name="workflow" value="reference_guided_original" hidden>
            <input id="workflow-original" type="radio" name="workflow" value="original" hidden>

            <label for="presetSelect">生产预设</label>
            <select id="presetSelect" name="preset_id">
              <option value="tutorial_longform" selected>发布增强</option>
              <option value="human_edit">真人剪辑</option>
              <option value="original_enhanced">原片增强</option>
              <option value="food_real_cut">美食真实剪辑</option>
            </select>

            <div class="production-grid">
              <div class="production-field">
                <label for="qualityStrictness">质量严格度</label>
                <select id="qualityStrictness" name="quality_strictness">
                  <option value="standard">标准</option>
                  <option value="strict">严格</option>
                  <option value="audit">企业审计</option>
                </select>
              </div>
              <div class="production-field">
                <label for="creativeStrength">创作强度</label>
                <select id="creativeStrength" name="creative_strength">
                  <option value="balanced">均衡</option>
                  <option value="light">轻剪辑</option>
                  <option value="strong">强创作</option>
                </select>
              </div>
              <div class="production-field">
                <label for="audioPolicy">音频策略</label>
                <select id="audioPolicy" name="audio_policy">
                  <option value="preserve_source">保留原声</option>
                  <option value="normalize_only">只做标准化</option>
                  <option value="replace_later">后续重配</option>
                </select>
              </div>
              <div class="production-field">
                <label for="visualTransformPolicy">视觉重构</label>
                <select id="visualTransformPolicy" name="visual_transform_policy">
                  <option value="none">不重构</option>
                  <option value="face_only">人像窗口修补（实验）</option>
                  <option value="remove_presenter">移除讲解人</option>
                </select>
              </div>
              <div class="production-field full">
                <label for="assetLibraryPath">素材库路径</label>
                <input id="assetLibraryPath" name="asset_library_path" type="text" placeholder="/Users/king/Videos/owned-assets">
                <p class="hint">只作为专家补位资源；发布增强会先分析原片，再决定是否插入自有素材、授权素材或少量 AI 补充镜头。</p>
              </div>
              <div class="production-field full">
                <label for="productionNotes">生产备注</label>
                <textarea id="productionNotes" name="production_notes" placeholder="例如：不要加包装，不要压得太短，保留教程完整感。"></textarea>
              </div>
            </div>

            <label class="replicate-only" id="replicateModeLabel">复刻模式</label>
            <div class="mode-grid replicate-only" id="replicateModeGrid">
              <input id="mode-auto" type="radio" name="mode" value="auto" checked>
              <label for="mode-auto"><strong>自动推荐</strong><span>根据时长选择增强或真人剪辑</span></label>
              <input id="mode-original" type="radio" name="mode" value="original-enhanced">
              <label for="mode-original"><strong>原片增强</strong><span>保留时长，只修音画</span></label>
              <input id="mode-human-edit" type="radio" name="mode" value="human-edit">
              <label for="mode-human-edit"><strong>真人剪辑</strong><span>切段、推近、输出 EDL</span></label>
              <input id="mode-creative" type="radio" name="mode" value="creative-edit">
              <label for="mode-creative"><strong>创作增强</strong><span>导演长版、模板预算、时长底线</span></label>
            </div>

            <div class="mode-inspector" id="modeBrief">
              <div class="mode-inspector-title">模式驾驶舱</div>
              <div class="mode-inspector-body" id="modeBriefBody"></div>
              <div class="mode-chip-list" id="modeBriefChips"></div>
            </div>

            <p class="hint" id="workflowHint">发布增强只处理自有或授权视频。系统会优先修好原片，再做必要的重排、AI 补充镜头、字幕、封面和质量检查。</p>
            <details class="hint">
              <summary>高级详情</summary>
              <p>后台文件包括 quality_report.json、originality_report.json、auto_repair_report.json、creative_plan.json、creative_strategy、cover_candidates.jpg、content_analysis.json、audio_analysis.json、semantic_timeline.json、transcript_analysis.json、candidate_edl.md、motion_plan.json、caption_timeline.json、subtitles.srt、voiceover_manifest.json、asset_pass_report.json、asset_usage_plan.json、visual_requirements.json、asset_sourcing_plan.json、visual_insert_plan.json、images2_prompt_pack.json、generated_visual_manifest.json、cover_brief.json、cover_prompt_pack.json、cover_asset_manifest.json、script、shotlist 和 asset_manifest；高级能力包含动态镜头、字幕时间线、旁白、封面生成、AI 补充镜头、自动返工和资产入镜。</p>
            </details>
          </div>
        </details>
      </form>

      <section class="panel">
        <div class="job-head">
          <h2>任务状态</h2>
          <div class="job-state" id="jobState">idle</div>
        </div>
        <details class="summary-board" id="summaryBoard">
          <summary class="summary-top">
            <span class="summary-kicker">验收面板</span>
            <div class="summary-actions">
              <span class="summary-status" id="summaryStatus">等待任务</span>
              <button class="secondary repair-action" id="repairButton" type="button" disabled>自动优化重做</button>
            </div>
          </summary>
          <div class="metric-grid" id="metricGrid"></div>
          <div class="check-grid" id="checkGrid"></div>
          <div class="issue-list" id="issueList">提交视频后，这里会显示比例、时长、模板风险、原创风险、相似度、音频复用和文本重合。</div>
        </details>
        <details class="queue-board" id="batchBoard">
          <summary class="board-title"><span>批次队列</span><span id="batchCount">0 条</span></summary>
          <div class="queue-list" id="batchList">
            <div class="hint">批量提交后，每条视频都会显示在这里。</div>
          </div>
        </details>
        <details class="history-board" id="historyBoard">
          <summary class="board-title"><span>任务历史</span><span id="historyCount">0 条</span></summary>
          <div class="history-list" id="historyList">
            <div class="hint">当前会话历史会显示在这里。</div>
          </div>
        </details>
        <div class="log" id="jobLog">等待创建任务...</div>
        <details class="artifact-details" id="artifactDetails">
          <summary><span class="artifact-summary-title">高级产物</span><span class="artifact-count" id="artifactCount">0 个</span></summary>
          <div class="artifacts" id="artifactList"></div>
        </details>
        <div class="preview" id="previewPane">
          <span class="hint">成片或质检图会显示在这里</span>
        </div>
        <div class="path-panel" id="filePathPanel">
          <div class="path-row">
            <span>视频文件</span>
            <code id="videoPathText">生成后显示 release.mp4 地址</code>
            <button class="secondary copy-path" type="button" data-copy-target="videoPathText">复制</button>
          </div>
          <div class="path-row">
            <span>所在目录</span>
            <code id="videoFolderText">生成后显示所在目录</code>
            <button class="secondary copy-path" type="button" data-copy-target="videoFolderText">复制</button>
          </div>
          <div class="path-row">
            <span>参考视频缓存</span>
            <code id="sourceCacheText">链接下载后显示 source/source.mp4 地址</code>
            <button class="secondary copy-path" type="button" data-copy-target="sourceCacheText">复制</button>
          </div>
          <div class="path-row no-copy">
            <span>下载状态</span>
            <code id="downloadStatusText">本地上传或本地路径不需要下载</code>
          </div>
          <div class="path-row">
            <span>原视频标题</span>
            <code id="sourceTitleText">链接下载后显示原视频标题</code>
            <button class="secondary copy-path" type="button" data-copy-target="sourceTitleText">复制</button>
          </div>
          <div class="path-row">
            <span>发布标题</span>
            <code id="publishTitleText">生成后显示推荐发布标题</code>
            <button class="secondary copy-path" type="button" data-copy-target="publishTitleText">复制</button>
          </div>
          <div class="title-candidates" id="titleCandidateList">
            <span class="hint">标题候选会显示在这里</span>
          </div>
        </div>
      </section>
    </section>
  </main>
  <div class="preview-modal" id="previewModal" aria-hidden="true">
    <div class="preview-modal-bar">
      <strong id="previewModalTitle">放大预览</strong>
      <button id="closePreviewButton" type="button">关闭</button>
    </div>
    <div class="preview-modal-body" id="previewModalBody"></div>
  </div>

  <script>
    const form = document.getElementById('jobForm');
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.getElementById('fileLabel');
    const inputPath = document.getElementById('inputPath');
    const sourceUrls = document.getElementById('sourceUrls');
    const startButton = document.getElementById('startButton');
    const clearButton = document.getElementById('clearButton');
    const jobState = document.getElementById('jobState');
    const jobLog = document.getElementById('jobLog');
    const artifactDetails = document.getElementById('artifactDetails');
    const artifactList = document.getElementById('artifactList');
    const artifactCount = document.getElementById('artifactCount');
    const previewPane = document.getElementById('previewPane');
    const videoPathText = document.getElementById('videoPathText');
    const videoFolderText = document.getElementById('videoFolderText');
    const sourceCacheText = document.getElementById('sourceCacheText');
    const downloadStatusText = document.getElementById('downloadStatusText');
    const sourceTitleText = document.getElementById('sourceTitleText');
    const publishTitleText = document.getElementById('publishTitleText');
    const titleCandidateList = document.getElementById('titleCandidateList');
    const previewModal = document.getElementById('previewModal');
    const previewModalBody = document.getElementById('previewModalBody');
    const previewModalTitle = document.getElementById('previewModalTitle');
    const closePreviewButton = document.getElementById('closePreviewButton');
    const modeBriefBody = document.getElementById('modeBriefBody');
    const modeBriefChips = document.getElementById('modeBriefChips');
    const summaryStatus = document.getElementById('summaryStatus');
    const metricGrid = document.getElementById('metricGrid');
    const checkGrid = document.getElementById('checkGrid');
    const issueList = document.getElementById('issueList');
    const presetSelect = document.getElementById('presetSelect');
    const qualityStrictness = document.getElementById('qualityStrictness');
    const creativeStrength = document.getElementById('creativeStrength');
    const durationPolicy = document.getElementById('durationPolicy');
    const audioPolicy = document.getElementById('audioPolicy');
    const visualTransformPolicy = document.getElementById('visualTransformPolicy');
    const visualAssetStrategy = document.getElementById('visualAssetStrategy');
    const productionNotes = document.getElementById('productionNotes');
    const sourceSection = document.getElementById('sourceSection');
    const originalSection = document.getElementById('originalSection');
    const originalTopic = document.getElementById('originalTopic');
    const originalBrief = document.getElementById('originalBrief');
    const assetLibraryPath = document.getElementById('assetLibraryPath');
    const replicateModeLabel = document.getElementById('replicateModeLabel');
    const replicateModeGrid = document.getElementById('replicateModeGrid');
    const workflowHint = document.getElementById('workflowHint');
    const repairButton = document.getElementById('repairButton');
    const batchList = document.getElementById('batchList');
    const batchCount = document.getElementById('batchCount');
    const historyList = document.getElementById('historyList');
    const historyCount = document.getElementById('historyCount');
    const PRESET_MODES = {
      foolproof_original: 'reference-guided-original',
      original_tutorial: 'original-generate',
      tutorial_longform: 'creative-edit',
      human_edit: 'human-edit',
      original_enhanced: 'original-enhanced',
      food_real_cut: 'creative-edit'
    };
    const PRESET_DEFAULTS = {
      foolproof_original: { quality: 'audit', creative: 'strong', duration: 'source_guided', audio: 'replace_later', visual: 'none', visualAsset: 'images2_first' },
      original_tutorial: { quality: 'audit', creative: 'strong', duration: 'source_guided', audio: 'replace_later', visual: 'none', visualAsset: 'images2_first' },
      tutorial_longform: { quality: 'audit', creative: 'strong', duration: 'source_guided', audio: 'normalize_only', visual: 'none', visualAsset: 'images2_contextual_inserts' },
      human_edit: { quality: 'standard', creative: 'light', duration: 'retain_core', audio: 'preserve_source', visual: 'none', visualAsset: 'user_owned_first' },
      original_enhanced: { quality: 'standard', creative: 'light', duration: 'keep_original', audio: 'preserve_source', visual: 'none', visualAsset: 'user_owned_first' },
      food_real_cut: { quality: 'audit', creative: 'strong', duration: 'source_guided', audio: 'replace_later', visual: 'none', visualAsset: 'images2_first' }
    };
    const MODE_BRIEFS = {
      auto: {
        body: '按源片时长、画面密度和可用证据自动选择路径，优先保证比例、成片可用和质量报告完整。',
        chips: ['自动分流', '比例保持', '产物完整', '失败自检']
      },
      'original-enhanced': {
        body: '保留源片叙事和时长，只做音画增强、封面候选、质检图和基础报告，适合样片本身已经很强的情况。',
        chips: ['不改结构', '音画修整', '封面候选', '基础质检']
      },
      'human-edit': {
        body: '从源片里找可剪片段，输出 candidate_edl.md 和接近真人剪辑的推近、节奏、保留/删除建议。',
        chips: ['切段判断', '推近节奏', 'EDL 输出', '源片画面']
      },
      'creative-edit': {
        body: '启用导演长版：结合内容、声音和语义时间线重排叙事，同时执行模板预算、时长底线和重复片段自检。',
        chips: ['导演长版', '模板预算', '时长底线', '语义章节', '重复检查']
      },
      'original-generate': {
        body: '旧版实验入口，仅用于内部验证脚本、分镜和素材清单，不作为当前主产品路径。',
        chips: ['内部实验', '脚本草案', '分镜草案', '上线闸门']
      },
      'reference-guided-original': {
        body: '旧版参考学习实验入口，当前前台不推荐使用；主流程已切回可落地的视频发布增强。',
        chips: ['内部实验', '参考拆解', '脚本草案', '发布结论']
      }
    };
    let pollTimer = null;
    let currentJobId = null;
    let batchJobIds = [];

    function setState(state) {
      jobState.textContent = state;
      jobState.className = 'job-state ' + state;
      startButton.disabled = state === 'running' || state === 'queued';
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function sanitizeLegacyLog(value) {
      const legacyReference = '一键' + '原创' + '视频';
      const legacyGenerate = '生成' + '原创' + '视频';
      return String(value || '')
        .replaceAll('完成' + legacyReference, '完成旧版参考学习实验')
        .replaceAll(legacyGenerate, '生成旧版实验样片')
        .replaceAll(legacyReference, '旧版参考学习实验');
    }

    function selectedWorkflow() {
      const checked = form.querySelector('input[name="workflow"]:checked');
      return checked ? checked.value : 'replicate';
    }

    function updateModeBrief() {
      const checked = form.querySelector('input[name="mode"]:checked');
      const workflow = selectedWorkflow();
      const mode = workflow === 'original'
        ? 'original-generate'
        : workflow === 'reference_guided_original'
          ? 'reference-guided-original'
          : (checked ? checked.value : 'auto');
      const brief = MODE_BRIEFS[mode] || MODE_BRIEFS.auto;
      modeBriefBody.textContent = brief.body;
      modeBriefChips.innerHTML = brief.chips
        .map((chip) => `<span class="mode-chip">${escapeHtml(chip)}</span>`)
        .join('');
    }

    function updateWorkflow() {
      const workflow = selectedWorkflow();
      const original = workflow === 'original';
      const guided = workflow === 'reference_guided_original';
      sourceSection.classList.toggle('is-hidden', original);
      originalSection.classList.toggle('is-hidden', false);
      replicateModeLabel.classList.toggle('is-hidden', original || guided);
      replicateModeGrid.classList.toggle('is-hidden', original || guided);
      startButton.textContent = original || guided ? '开始旧版实验' : '开始发布增强';
      workflowHint.textContent = guided
        ? '旧版参考学习实验会输出 reference_blueprint、content_plan、script_v2、storyboard_v2、visual_requirements、asset_sourcing_plan、cover_brief、cover_prompt_pack、cover_asset_manifest、visual_prompt_pack、generated_asset_manifest、caption_timeline、subtitles、voiceover_manifest、user_delivery 和质量报告；当前主产品先不使用这条路。'
        : original
          ? '旧版无参考实验会从选题生成脚本、分镜、素材需求、字幕、配音清单和自检报告；当前主产品先不使用这条路。'
          : '发布增强只处理自有或授权视频。复杂片可以先看 creative_plan、semantic_timeline、transcript_analysis、candidate_edl、originality_report 和自检报告再细调。';
      inputPath.placeholder = guided
        ? '/Users/king/Downloads/reference.mp4'
        : '/Users/king/Downloads/example-a.mp4\\n/Users/king/Downloads/example-b.mp4';
      fileLabel.textContent = guided && !fileInput.files.length ? '拖入参考视频，或点击选择' : fileLabel.textContent;
      if (original && presetSelect.value !== 'original_tutorial') {
        presetSelect.value = 'original_tutorial';
        applyPresetDefaults();
      }
      if (guided && presetSelect.value !== 'foolproof_original') {
        presetSelect.value = 'foolproof_original';
        applyPresetDefaults();
      }
      if (!original && presetSelect.value === 'original_tutorial') {
        presetSelect.value = 'tutorial_longform';
        applyPresetDefaults();
      }
      if (!guided && presetSelect.value === 'foolproof_original') {
        presetSelect.value = original ? 'original_tutorial' : 'tutorial_longform';
        applyPresetDefaults();
      }
      updateModeBrief();
    }

    function resetSummary(message = '提交视频后，这里会显示比例、时长、模板风险和导演策略的真实验收结果。') {
      summaryStatus.textContent = '等待任务';
      summaryStatus.className = 'summary-status';
      repairButton.disabled = true;
      metricGrid.innerHTML = `
        <div class="metric"><span>质量分</span><strong>等待生成</strong></div>
        <div class="metric"><span>原创风险</span><strong>等待生成</strong></div>
        <div class="metric"><span>音频复用</span><strong>等待生成</strong></div>
      `;
      checkGrid.innerHTML = ['比例保持', '无包装卡片', '模板预算', '时长底线', '导演动作', '证据链']
        .map((label) => `<div class="check-pill"><span>${label}</span><span class="check-mark">--</span></div>`)
        .join('');
      issueList.textContent = message || '提交视频后，这里会显示比例、时长、模板风险、原创风险、相似度、音频复用和文本重合。';
    }

    function dirnameFromPath(pathValue) {
      const value = String(pathValue || '');
      const slash = Math.max(value.lastIndexOf('/'), value.lastIndexOf('\\\\'));
      return slash > 0 ? value.slice(0, slash) : '';
    }

    function setVideoFilePath(videoPath) {
      const value = String(videoPath || '').trim();
      if (!value) {
        videoPathText.textContent = '生成后显示 release.mp4 地址';
        videoFolderText.textContent = '生成后显示所在目录';
        return;
      }
      videoPathText.textContent = value;
      videoFolderText.textContent = dirnameFromPath(value) || value;
    }

    function setSourceDownloadStatus(job) {
      const sourceUrl = job && job.options ? String(job.options.source_url || '').trim() : '';
      const sourcePath = job && job.artifacts ? String(job.artifacts.source_video || '').trim() : '';
      const sourceReport = job && job.artifacts ? String(job.artifacts.source_download || '').trim() : '';
      const status = job ? String(job.status || '') : '';
      const error = job ? String(job.error || '').trim() : '';
      if (sourcePath) {
        sourceCacheText.textContent = sourcePath;
        downloadStatusText.textContent = '下载完成';
        return;
      }
      if (sourceUrl && status === 'error') {
        sourceCacheText.textContent = sourceReport || '下载失败，未生成缓存视频';
        downloadStatusText.textContent = error ? `下载失败：${error}` : '下载失败，请打开 source_download 查看原因';
        return;
      }
      if (sourceUrl && (status === 'running' || status === 'queued')) {
        sourceCacheText.textContent = '下载中，完成后显示 source/source.mp4 地址';
        downloadStatusText.textContent = status === 'queued' ? '等待解析链接' : '解析链接 / 下载视频 / 开始生成';
        return;
      }
      if (sourceUrl) {
        sourceCacheText.textContent = sourceReport || '链接下载后显示 source/source.mp4 地址';
        downloadStatusText.textContent = '等待下载';
        return;
      }
      sourceCacheText.textContent = '链接下载后显示 source/source.mp4 地址';
      downloadStatusText.textContent = '本地上传或本地路径不需要下载';
    }

    function setTitleMetadata(job) {
      const metadata = job && job.source_metadata ? job.source_metadata : {};
      const sourceTitle = String(metadata.source_title || '').trim();
      const publishTitle = String(metadata.recommended_publish_title || '').trim();
      const candidates = Array.isArray(metadata.publish_title_candidates)
        ? metadata.publish_title_candidates.filter((item) => String(item || '').trim())
        : [];
      sourceTitleText.textContent = sourceTitle || '链接下载后显示原视频标题';
      publishTitleText.textContent = publishTitle || '生成后显示推荐发布标题';
      titleCandidateList.innerHTML = candidates.length
        ? candidates.map((title) => `
            <button class="title-candidate" type="button" data-title="${escapeHtml(title)}">${escapeHtml(title)}</button>
          `).join('')
        : '<span class="hint">标题候选会显示在这里</span>';
    }

    function renderSummary(summary, status) {
      if (!summary || !Object.keys(summary).length) {
        resetSummary(status === 'running' || status === 'queued' ? '任务正在生成，完成后会自动刷新验收结果。' : undefined);
        return;
      }
      const passed = summary.status === 'passed';
      summaryStatus.textContent = typeof summary.score === 'number' ? `${summary.score}分 ${summary.grade}` : (passed ? '通过' : '需处理');
      summaryStatus.className = 'summary-status ' + (passed ? 'pass' : 'fail');
      repairButton.disabled = !currentJobId;
      const metrics = Array.isArray(summary.metrics) && summary.metrics.length ? [...summary.metrics] : [
        { label: '质量状态', value: summary.status || 'unknown' }
      ];
      if (typeof summary.score === 'number') {
        metrics.unshift({ label: '质量分', value: `${summary.score} / ${summary.grade} / ${summary.risk_level}` });
      }
      metricGrid.innerHTML = metrics
        .map((metric) => `
          <div class="metric">
            <span>${escapeHtml(metric.label)}</span>
            <strong>${escapeHtml(metric.value)}</strong>
          </div>
        `)
        .join('');
      const checks = Array.isArray(summary.checks) ? summary.checks : [];
      checkGrid.innerHTML = checks.length
        ? checks.map((check) => `
            <div class="check-pill ${check.passed ? 'pass' : 'fail'}">
              <span>${escapeHtml(check.label)}</span>
              <span class="check-mark">${check.passed ? 'OK' : '修'}</span>
            </div>
          `).join('')
        : '<div class="check-pill"><span>暂无检查项</span><span class="check-mark">--</span></div>';
      const issues = Array.isArray(summary.issues) ? summary.issues : [];
      if (issues.length) {
        issueList.innerHTML = issues
          .map((issue) => `<div><strong>${escapeHtml(issue.code)}</strong> ${escapeHtml(issue.message)}</div>`)
          .join('');
      } else if (summary.originality && summary.originality.recommendations && summary.originality.recommendations.length && summary.originality.risk_level !== 'low') {
        issueList.textContent = '原创改造建议：' + summary.originality.recommendations.join(' / ');
      } else if (summary.repair_suggestions && summary.repair_suggestions.length) {
        issueList.textContent = '修复建议：' + summary.repair_suggestions.join(' / ');
      } else if (summary.strategy && summary.strategy.moves && summary.strategy.moves.length) {
        issueList.textContent = '导演动作：' + summary.strategy.moves.join(' / ');
      } else {
        issueList.textContent = passed ? '未发现阻断问题。' : '质量报告未给出具体问题，请打开 quality_report.json 查看。';
      }
    }

    function renderJob(job) {
      currentJobId = job.id;
      setState(job.status);
      renderSummary(job.quality_summary, job.status);
      const strategy = job.options
        ? [
            `生产入口: ${job.options.workflow || 'replicate'}`,
            `创作强度: ${job.options.creative_strength || '-'}`,
            `音频策略: ${job.options.audio_policy || '-'}`,
            `画面策略: ${job.options.visual_asset_strategy || 'images2_first'}`,
            `视觉重构: ${job.options.visual_transform_policy || 'none'}`,
            `修复焦点: ${(job.options.repair_focus || []).join(',') || '-'}`
          ]
        : [];
      const notes = job.options && job.options.production_notes ? [`生产备注: ${job.options.production_notes}`] : [];
      const lines = [`任务: ${job.id}`, `模式: ${job.mode}`, `视频: ${job.source_name}`]
        .concat(strategy, notes, [''])
        .concat(job.logs || []);
      if (job.error) lines.push('', 'ERROR: ' + job.error);
      jobLog.textContent = sanitizeLegacyLog(lines.join('\\n'));
      setVideoFilePath(job.artifacts && job.artifacts.video ? job.artifacts.video : '');
      setSourceDownloadStatus(job);
      setTitleMetadata(job);
      artifactList.innerHTML = '';
      artifactCount.textContent = '0 个';
      artifactDetails.open = false;
      if (job.artifact_urls) {
        const artifactEntries = Object.entries(job.artifact_urls);
        artifactCount.textContent = `${artifactEntries.length} 个`;
        for (const [name, url] of artifactEntries) {
          const a = document.createElement('a');
          a.className = 'artifact';
          a.href = url;
          a.target = '_blank';
          a.textContent = name;
          artifactList.appendChild(a);
        }
        if (job.artifact_urls.video) {
          previewPane.innerHTML = previewMarkup('video', job.artifact_urls.video, '成片预览', job.artifact_urls.cover || '');
        } else if (job.artifact_urls.contact_sheet) {
          previewPane.innerHTML = previewMarkup('image', job.artifact_urls.contact_sheet, '质检图预览');
        }
      }
    }

    function previewMarkup(kind, url, label, posterUrl = '') {
      const safeUrl = escapeHtml(url);
      const safeLabel = escapeHtml(label);
      const safePoster = escapeHtml(posterUrl);
      const media = kind === 'video'
        ? `<video controls preload="metadata" ${safePoster ? `poster="${safePoster}"` : ''} src="${safeUrl}"></video>`
        : `<img src="${safeUrl}" alt="${safeLabel}">`;
      return `
        <div class="preview-shell">
          <div class="preview-tools">
            <button class="preview-action expand-preview" id="expandPreviewButton" type="button" data-kind="${kind}" data-url="${safeUrl}" data-label="${safeLabel}" data-poster="${safePoster}">放大预览</button>
            <a href="${safeUrl}" target="_blank" rel="noreferrer">新窗口打开</a>
          </div>
          ${media}
        </div>
      `;
    }

    function openPreview(kind, url, label, posterUrl = '') {
      previewModalTitle.textContent = label || '放大预览';
      const safeUrl = escapeHtml(url);
      const safeLabel = escapeHtml(label || 'preview');
      const safePoster = escapeHtml(posterUrl);
      previewModalBody.innerHTML = kind === 'video'
        ? `<video controls autoplay preload="metadata" ${safePoster ? `poster="${safePoster}"` : ''} src="${safeUrl}"></video>`
        : `<img src="${safeUrl}" alt="${safeLabel}">`;
      previewModal.classList.add('open');
      previewModal.setAttribute('aria-hidden', 'false');
    }

    function closePreview() {
      previewModal.classList.remove('open');
      previewModal.setAttribute('aria-hidden', 'true');
      previewModalBody.innerHTML = '';
    }

    async function pollJob(id) {
      const res = await fetch('/api/jobs/' + encodeURIComponent(id));
      const job = await res.json();
      renderJob(job);
      if (job.status === 'done' || job.status === 'error') {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function renderBatch(jobs) {
      const list = Array.isArray(jobs) ? jobs : [];
      batchCount.textContent = `${list.length} 条`;
      if (!list.length) {
        batchList.innerHTML = '<div class="hint">批量提交后，每条视频都会显示在这里。</div>';
        return;
      }
      batchList.innerHTML = list.map((job) => `
        <div class="queue-item">
          <div>
            <strong>${escapeHtml(job.source_name)}</strong>
            <span>${escapeHtml(job.mode)} · ${escapeHtml(job.options && job.options.preset_label)}</span>
          </div>
          <span class="mini-state ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
        </div>
      `).join('');
    }

    async function pollBatch() {
      if (!batchJobIds.length) return;
      const jobs = await Promise.all(batchJobIds.map(async (id) => {
        const res = await fetch('/api/jobs/' + encodeURIComponent(id));
        return res.json();
      }));
      renderBatch(jobs);
      const primary = jobs.find((job) => job.id === currentJobId) || jobs[0];
      renderJob(primary);
      const done = jobs.every((job) => job.status === 'done' || job.status === 'error');
      if (done) {
        clearInterval(pollTimer);
        pollTimer = null;
        refreshHistory();
      }
    }

    async function refreshHistory() {
      const res = await fetch('/api/jobs');
      const payload = await res.json();
      const jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
      historyCount.textContent = `${jobs.length} 条`;
      if (!jobs.length) {
        historyList.innerHTML = '<div class="hint">当前会话历史会显示在这里。</div>';
        return;
      }
      historyList.innerHTML = jobs.slice(0, 12).map((job) => {
        const score = job.quality_summary && typeof job.quality_summary.score === 'number'
          ? `${job.quality_summary.score}分`
          : '未评分';
        return `
          <div class="history-item" data-job-id="${escapeHtml(job.id)}">
            <div>
              <strong>${escapeHtml(job.source_name)}</strong>
              <span>${escapeHtml(job.mode)} · ${escapeHtml(score)}</span>
            </div>
            <span class="mini-state ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
          </div>
        `;
      }).join('');
    }

    historyList.addEventListener('click', async (event) => {
      const item = event.target.closest('.history-item');
      if (!item || !item.dataset.jobId) return;
      const res = await fetch('/api/jobs/' + encodeURIComponent(item.dataset.jobId));
      const job = await res.json();
      batchJobIds = [job.id];
      currentJobId = job.id;
      renderBatch([job]);
      renderJob(job);
    });

    function applyPresetDefaults() {
      const preset = presetSelect.value;
      const defaults = PRESET_DEFAULTS[preset] || PRESET_DEFAULTS.tutorial_longform;
      qualityStrictness.value = defaults.quality;
      creativeStrength.value = defaults.creative;
      durationPolicy.value = defaults.duration;
      audioPolicy.value = defaults.audio;
      visualTransformPolicy.value = defaults.visual;
      visualAssetStrategy.value = defaults.visualAsset;
    }

    function applyPreset() {
      const preset = presetSelect.value;
      const workflowValue = preset === 'foolproof_original'
        ? 'reference_guided_original'
        : preset === 'original_tutorial'
          ? 'original'
          : 'replicate';
      const workflowRadio = form.querySelector(`input[name="workflow"][value="${workflowValue}"]`);
      if (workflowRadio) workflowRadio.checked = true;
      const mode = PRESET_MODES[preset] || 'auto';
      const radio = form.querySelector(`input[name="mode"][value="${mode}"]`);
      if (radio) radio.checked = true;
      applyPresetDefaults();
      updateWorkflow();
      updateModeBrief();
    }

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (event) => {
      event.preventDefault();
      dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (event) => {
      event.preventDefault();
      dropZone.classList.remove('dragover');
      if (event.dataTransfer.files.length) {
        fileInput.files = event.dataTransfer.files;
        fileLabel.textContent = event.dataTransfer.files.length === 1
          ? event.dataTransfer.files[0].name
          : `${event.dataTransfer.files.length} 个视频已选择`;
      }
    });
    fileInput.addEventListener('change', () => {
      fileLabel.textContent = fileInput.files.length
        ? (fileInput.files.length === 1 ? fileInput.files[0].name : `${fileInput.files.length} 个视频已选择`)
        : '拖入一批视频，或点击选择';
    });
    form.querySelectorAll('input[name="mode"]').forEach((input) => {
      input.addEventListener('change', updateModeBrief);
    });
    form.querySelectorAll('input[name="workflow"]').forEach((input) => {
      input.addEventListener('change', updateWorkflow);
    });
    presetSelect.addEventListener('change', applyPreset);

    previewPane.addEventListener('click', (event) => {
      const button = event.target.closest('.expand-preview');
      if (!button) return;
      openPreview(button.dataset.kind, button.dataset.url, button.dataset.label, button.dataset.poster || '');
    });
    document.querySelectorAll('.copy-path').forEach((button) => {
      button.addEventListener('click', async () => {
        const target = document.getElementById(button.dataset.copyTarget || '');
        const value = target ? target.textContent.trim() : '';
        if (!value || value.startsWith('生成后') || value.startsWith('链接下载后') || value.startsWith('下载中') || value.startsWith('下载失败')) return;
        try {
          await navigator.clipboard.writeText(value);
          const originalText = button.textContent;
          button.textContent = '已复制';
          setTimeout(() => {
            button.textContent = originalText;
          }, 1200);
        } catch (error) {
          const selection = window.getSelection();
          const range = document.createRange();
          range.selectNodeContents(target);
          selection.removeAllRanges();
          selection.addRange(range);
        }
      });
    });
    titleCandidateList.addEventListener('click', async (event) => {
      const button = event.target.closest('.title-candidate');
      if (!button || !button.dataset.title) return;
      publishTitleText.textContent = button.dataset.title;
      try {
        await navigator.clipboard.writeText(button.dataset.title);
        const originalText = button.textContent;
        button.textContent = '已复制：' + originalText;
        setTimeout(() => {
          button.textContent = originalText;
        }, 1200);
      } catch (error) {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(publishTitleText);
        selection.removeAllRanges();
        selection.addRange(range);
      }
    });
    closePreviewButton.addEventListener('click', closePreview);
    previewModal.addEventListener('click', (event) => {
      if (event.target === previewModal) closePreview();
    });
    window.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closePreview();
    });

    clearButton.addEventListener('click', () => {
      fileInput.value = '';
      inputPath.value = '';
      sourceUrls.value = '';
      originalTopic.value = '';
      originalBrief.value = '';
      assetLibraryPath.value = '';
      visualAssetStrategy.value = 'images2_contextual_inserts';
      fileLabel.textContent = '拖入一批视频，或点击选择';
      form.querySelector('input[name="workflow"][value="replicate"]').checked = true;
      presetSelect.value = 'tutorial_longform';
      applyPresetDefaults();
      updateWorkflow();
      artifactList.innerHTML = '';
      artifactCount.textContent = '0 个';
      artifactDetails.open = false;
      batchJobIds = [];
      currentJobId = null;
      renderBatch([]);
      previewPane.innerHTML = '<span class="hint">成片或质检图会显示在这里</span>';
      setVideoFilePath('');
      setSourceDownloadStatus(null);
      setTitleMetadata(null);
      jobLog.textContent = '等待创建任务...';
      resetSummary();
      setState('idle');
    });

    repairButton.addEventListener('click', async () => {
      if (!currentJobId) return;
      repairButton.disabled = true;
      const res = await fetch('/api/jobs/' + encodeURIComponent(currentJobId) + '/repair', { method: 'POST' });
      const payload = await res.json();
      if (!res.ok) {
        jobLog.textContent = payload.error || '创建返工任务失败';
        repairButton.disabled = false;
        return;
      }
      batchJobIds = [payload.id];
      currentJobId = payload.id;
      renderBatch([payload]);
      renderJob(payload);
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollBatch, 1400);
      pollBatch();
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      setState('queued');
      jobLog.textContent = '提交任务...';
      const res = await fetch('/api/jobs', { method: 'POST', body: data });
      const payload = await res.json();
      if (!res.ok) {
        setState('error');
        jobLog.textContent = payload.error || '创建任务失败';
        return;
      }
      const jobs = Array.isArray(payload.jobs) ? payload.jobs : [payload];
      batchJobIds = jobs.map((job) => job.id);
      currentJobId = payload.primary_job_id || jobs[0].id;
      renderBatch(jobs);
      renderJob(jobs[0]);
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollBatch, 1400);
      pollBatch();
    });
    applyPreset();
    updateModeBrief();
    resetSummary();
    refreshHistory();
  </script>
</body>
</html>
"""


@dataclass(frozen=True)
class ParsedJobRequest:
    status: HTTPStatus
    mode: str = "auto"
    input_path: Path | None = None
    input_paths: tuple[Path, ...] = ()
    source_urls: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()
    options: dict = field(default_factory=dict)
    source_name: str = ""
    error: str = ""


def _refresh_loaded_job_quality(job: dict) -> dict:
    refreshed = dict(job)
    artifacts = refreshed.get("artifacts", {}) if isinstance(refreshed.get("artifacts", {}), dict) else {}
    if artifacts.get("quality_report"):
        refreshed["quality_summary"] = build_quality_summary(
            {str(key): str(value) for key, value in artifacts.items()},
            options=refreshed.get("options", {}) if isinstance(refreshed.get("options", {}), dict) else {},
        )
    return refreshed


class JobStore:
    def __init__(self, history_path: Path | None = None) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._history_path = history_path
        if self._history_path and self._history_path.exists():
            try:
                payload = json.loads(self._history_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            for job in payload.get("jobs", []):
                if isinstance(job, dict) and job.get("id"):
                    self._jobs[str(job["id"])] = _refresh_loaded_job_quality(job)

    def create(
        self,
        mode: str,
        source_name: str,
        input_path: str = "",
        batch_id: str = "",
        options: dict | None = None,
        repair_of: str = "",
    ) -> dict:
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = {
            "id": job_id,
            "mode": mode,
            "source_name": source_name,
            "input_path": input_path,
            "batch_id": batch_id,
            "repair_of": repair_of,
            "options": dict(options or {}),
            "status": "queued",
            "created_at": time.time(),
            "logs": [],
            "artifacts": {},
            "artifact_urls": {},
            "quality_summary": {},
            "error": "",
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist_locked()
        return dict(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.get("created_at", 0), reverse=True)
            return [dict(job) for job in jobs[:limit]]

    def create_repair(self, job_id: str) -> dict | None:
        with self._lock:
            original = self._jobs.get(job_id)
            if original is None:
                return None
        options = _repair_options_from_job(original)
        return self.create(
            mode=str(options.get("mode") or original.get("mode", "auto")),
            source_name=str(original.get("source_name", "")),
            input_path=str(original.get("input_path", "")),
            batch_id=time.strftime("repair-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
            options=options,
            repair_of=job_id,
        )

    def update(self, job_id: str, **changes) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)
            self._persist_locked()

    def log(self, job_id: str, message: str) -> None:
        with self._lock:
            self._jobs[job_id].setdefault("logs", []).append(message)
            self._persist_locked()

    def _persist_locked(self) -> None:
        if self._history_path is None:
            return
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": self.list_locked()}
        tmp_path = self._history_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._history_path)

    def list_locked(self, limit: int = 200) -> list[dict]:
        jobs = sorted(self._jobs.values(), key=lambda job: job.get("created_at", 0), reverse=True)
        return [dict(job) for job in jobs[:limit]]


def parse_job_request(
    content_type: str,
    body: BinaryIO,
    content_length: int,
    upload_dir: Path | None = None,
) -> ParsedJobRequest:
    if content_length <= 0:
        return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供视频文件、本地视频路径或视频链接。")

    if content_type.startswith("application/json"):
        payload = json.loads(body.read(content_length) or b"{}")
        options = build_production_options(payload)
        mode = payload.get("mode") or options["mode"]
        source_urls = tuple(parse_source_urls(payload.get("source_urls", "")))
        source_url_names = tuple(_source_name_from_url(url) for url in source_urls)
        if options.get("workflow") == "reference_guided_original" or mode == "reference-guided-original":
            options["workflow"] = "reference_guided_original"
            options["mode"] = "reference-guided-original"
            paths = _parse_path_lines(str(payload.get("input_path", "")).strip())
            if not paths and not source_urls:
                return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供参考视频或视频链接。")
            return ParsedJobRequest(
                status=HTTPStatus.OK,
                mode="reference-guided-original",
                input_path=paths[0] if paths else None,
                input_paths=tuple(paths),
                source_urls=source_urls,
                source_names=tuple(path.name for path in paths) + source_url_names,
                options=options,
                source_name=paths[0].name if paths else source_url_names[0],
            )
        if options.get("workflow") == "original" or mode == "original-generate":
            options["workflow"] = "original"
            options["mode"] = "original-generate"
            topic = str(options.get("original_topic") or "").strip()
            brief = str(options.get("original_brief") or "").strip()
            if not topic and not brief:
                return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供原创选题或原创创作说明。")
            source_name = topic or brief[:60] or "无参考原创任务"
            return ParsedJobRequest(
                status=HTTPStatus.OK,
                mode="original-generate",
                input_path=None,
                input_paths=(),
                source_names=(source_name,),
                options=options,
                source_name=source_name,
            )
        input_value = str(payload.get("input_path", "")).strip()
        if mode not in MODE_CHOICES:
            return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="不支持的复刻模式。")
        paths = _parse_path_lines(input_value)
        if not paths and not source_urls:
            return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供视频文件、本地视频路径或视频链接。")
        return ParsedJobRequest(
            status=HTTPStatus.OK,
            mode=mode,
            input_path=paths[0] if paths else None,
            input_paths=tuple(paths),
            source_urls=source_urls,
            source_names=tuple(path.name for path in paths) + source_url_names,
            options=options,
            source_name=paths[0].name if paths else source_url_names[0],
        )

    if content_type.startswith("multipart/form-data"):
        upload_dir = upload_dir or UPLOAD_ROOT
        upload_dir.mkdir(parents=True, exist_ok=True)
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(content_length),
        }
        form = cgi.FieldStorage(fp=body, environ=environ, keep_blank_values=True)
        form_payload = {
            "workflow": form.getvalue("workflow", ""),
            "preset_id": form.getvalue("preset_id", ""),
            "quality_strictness": form.getvalue("quality_strictness", ""),
            "creative_strength": form.getvalue("creative_strength", ""),
            "target_duration_policy": form.getvalue("target_duration_policy", ""),
            "audio_policy": form.getvalue("audio_policy", ""),
            "visual_transform_policy": form.getvalue("visual_transform_policy", ""),
            "visual_asset_strategy": form.getvalue("visual_asset_strategy", ""),
            "topic": form.getvalue("topic", ""),
            "original_topic": form.getvalue("original_topic", ""),
            "original_brief": form.getvalue("original_brief", ""),
            "asset_library_path": form.getvalue("asset_library_path", ""),
            "production_notes": form.getvalue("production_notes", ""),
            "source_urls": form.getvalue("source_urls", ""),
        }
        options = build_production_options(form_payload)
        mode = form.getvalue("mode", "") or options["mode"]
        source_urls = tuple(parse_source_urls(form_payload["source_urls"]))
        source_url_names = tuple(_source_name_from_url(url) for url in source_urls)
        if options.get("workflow") == "reference_guided_original" or mode == "reference-guided-original":
            options["workflow"] = "reference_guided_original"
            options["mode"] = "reference-guided-original"
            input_value = str(form.getvalue("input_path", "")).strip()
            file_items = _uploaded_file_items(form)
            upload_paths: list[Path] = []
            source_names: list[str] = []
            for file_item in file_items:
                safe_name = _safe_upload_name(file_item.filename)
                path = upload_dir / f"{int(time.time())}-{uuid.uuid4().hex[:6]}-{safe_name}"
                with path.open("wb") as output:
                    shutil.copyfileobj(file_item.file, output)
                upload_paths.append(path)
                source_names.append(str(file_item.filename))
            local_paths = _parse_path_lines(input_value)
            paths = upload_paths + local_paths
            source_names.extend(path.name for path in local_paths)
            source_names.extend(source_url_names)
            if not paths and not source_urls:
                return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供参考视频或视频链接。")
            return ParsedJobRequest(
                status=HTTPStatus.OK,
                mode="reference-guided-original",
                input_path=paths[0] if paths else None,
                input_paths=tuple(paths),
                source_urls=source_urls,
                source_names=tuple(source_names),
                options=options,
                source_name=source_names[0],
            )
        if options.get("workflow") == "original" or mode == "original-generate":
            options["workflow"] = "original"
            options["mode"] = "original-generate"
            topic = str(options.get("original_topic") or "").strip()
            brief = str(options.get("original_brief") or "").strip()
            if not topic and not brief:
                return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供原创选题或原创创作说明。")
            source_name = topic or brief[:60] or "无参考原创任务"
            return ParsedJobRequest(
                status=HTTPStatus.OK,
                mode="original-generate",
                input_path=None,
                input_paths=(),
                source_names=(source_name,),
                options=options,
                source_name=source_name,
            )
        input_value = str(form.getvalue("input_path", "")).strip()
        if mode not in MODE_CHOICES:
            return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="不支持的复刻模式。")
        file_items = _uploaded_file_items(form)
        upload_paths: list[Path] = []
        source_names: list[str] = []
        for file_item in file_items:
            safe_name = _safe_upload_name(file_item.filename)
            path = upload_dir / f"{int(time.time())}-{uuid.uuid4().hex[:6]}-{safe_name}"
            with path.open("wb") as output:
                shutil.copyfileobj(file_item.file, output)
            upload_paths.append(path)
            source_names.append(str(file_item.filename))
        local_paths = _parse_path_lines(input_value)
        paths = upload_paths + local_paths
        source_names.extend(path.name for path in local_paths)
        source_names.extend(source_url_names)
        if paths or source_urls:
            return ParsedJobRequest(
                status=HTTPStatus.OK,
                mode=mode,
                input_path=paths[0] if paths else None,
                input_paths=tuple(paths),
                source_urls=source_urls,
                source_names=tuple(source_names),
                options=options,
                source_name=source_names[0],
            )
        return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="请提供视频文件、本地视频路径或视频链接。")

    return ParsedJobRequest(status=HTTPStatus.BAD_REQUEST, error="不支持的请求格式。")


def build_production_options(payload: dict) -> dict:
    workflow = _choice(str(payload.get("workflow", "")).strip(), WORKFLOW_CHOICES, "replicate")
    requested_preset_id = str(payload.get("preset_id", "")).strip()
    preset_id = _choice(requested_preset_id, set(PRODUCTION_PRESETS), DEFAULT_PRESET_ID)
    if workflow == "reference_guided_original" and requested_preset_id not in PRODUCTION_PRESETS:
        preset_id = "foolproof_original"
    preset = PRODUCTION_PRESETS[preset_id]
    quality_strictness = _choice(
        str(payload.get("quality_strictness", "")).strip(),
        QUALITY_STRICTNESS_CHOICES,
        str(preset.get("quality_strictness", "standard")),
    )
    creative_strength = _choice(
        str(payload.get("creative_strength", "")).strip(),
        CREATIVE_STRENGTH_CHOICES,
        str(preset.get("creative_strength", "balanced")),
    )
    target_duration_policy = _choice(
        str(payload.get("target_duration_policy", "")).strip(),
        TARGET_DURATION_POLICIES,
        str(preset.get("target_duration_policy", "source_guided")),
    )
    audio_policy = _choice(
        str(payload.get("audio_policy", "")).strip(),
        AUDIO_POLICY_CHOICES,
        str(preset.get("audio_policy", "preserve_source")),
    )
    visual_transform_policy = _choice(
        str(payload.get("visual_transform_policy", "")).strip(),
        VISUAL_TRANSFORM_POLICY_CHOICES,
        "none",
    )
    visual_asset_strategy = _choice(
        str(payload.get("visual_asset_strategy", "")).strip(),
        VISUAL_ASSET_STRATEGY_CHOICES,
        str(preset.get("visual_asset_strategy", "images2_first")),
    )
    options = {
        "workflow": workflow,
        "preset_id": preset_id,
        "preset_label": preset["label"],
        "mode": preset["mode"],
        "quality_strictness": quality_strictness,
        "creative_strength": creative_strength,
        "target_duration_policy": target_duration_policy,
        "audio_policy": audio_policy,
        "visual_transform_policy": visual_transform_policy,
        "visual_asset_strategy": visual_asset_strategy,
        "original_insert_policy": str(preset.get("original_insert_policy", "none")),
        "original_topic": str(payload.get("original_topic") or payload.get("topic") or "").strip()[:180],
        "original_brief": str(payload.get("original_brief", "")).strip()[:1200],
        "asset_library_path": str(payload.get("asset_library_path", "")).strip()[:1000],
        "production_notes": str(payload.get("production_notes", "")).strip()[:500],
    }
    if workflow == "reference_guided_original":
        options.update(
            {
                "mode": "reference-guided-original",
                "reuse_policy": "redraw_by_default",
                "visual_style": "documentary_illustration",
                "content_depth": str(payload.get("content_depth") or "standard").strip()[:80] or "standard",
                "audience": str(payload.get("audience") or "auto").strip()[:120] or "auto",
                "platform": str(payload.get("platform") or "short_video").strip()[:80] or "short_video",
                "image_provider": str(payload.get("image_provider") or "mock_images2").strip()[:120] or "mock_images2",
                "voice_provider": str(payload.get("voice_provider") or "mock_professional_voice").strip()[:120]
                or "mock_professional_voice",
            }
        )
        if str(payload.get("target_duration_seconds") or "").strip():
            try:
                options["target_duration_seconds"] = float(payload["target_duration_seconds"])
            except (TypeError, ValueError):
                pass
        duration_range = payload.get("duration_range_seconds")
        if isinstance(duration_range, list) and len(duration_range) >= 2:
            try:
                options["duration_range_seconds"] = [float(duration_range[0]), float(duration_range[1])]
            except (TypeError, ValueError):
                pass
    return options


def _repair_options_from_job(job: dict) -> dict:
    options = dict(job.get("options", {}))
    summary = job.get("quality_summary", {}) if isinstance(job.get("quality_summary", {}), dict) else {}
    deductions = summary.get("deductions", []) if isinstance(summary.get("deductions", []), list) else []
    suggestions = summary.get("repair_suggestions", []) if isinstance(summary.get("repair_suggestions", []), list) else []
    originality = summary.get("originality", {}) if isinstance(summary.get("originality", {}), dict) else {}
    originality_suggestions = (
        originality.get("recommendations", [])
        if isinstance(originality.get("recommendations", []), list)
        else []
    )
    codes = [str(deduction.get("code", "")) for deduction in deductions if isinstance(deduction, dict)]
    focus: list[str] = []
    if any("template" in code or "generated_cards" in code for code in codes):
        focus.append("template")
        options["creative_strength"] = "strong"
    if any("duration" in code or "longform" in code for code in codes):
        focus.append("duration")
        options["target_duration_policy"] = "retain_core"
    if any("geometry" in code for code in codes):
        focus.append("geometry")
    if any("semantic" in code or "chronology" in code for code in codes):
        focus.append("semantic")
    if any("audio" in code for code in codes):
        focus.append("audio")
    if originality.get("risk_level") in {"medium", "high"}:
        focus.append("originality")
        options["mode"] = "creative-edit"
        options["quality_strictness"] = "audit"
        options["creative_strength"] = "strong"
        options["audio_policy"] = "replace_later"
        options["visual_transform_policy"] = "none"
        options["target_duration_policy"] = "retain_core"
    if codes:
        options["quality_strictness"] = "audit"
    options["repair_reason"] = "quality_followup"
    if focus:
        options["repair_focus"] = focus
    notes = list(suggestions) + [str(item) for item in originality_suggestions]
    if originality.get("risk_level") in {"medium", "high"}:
        notes.append("不要使用卡通化、线稿滤镜或假脸贴片，只用真实源片重组并等待原创音频。")
    if notes:
        existing_notes = str(options.get("production_notes", "")).strip()
        repair_notes = "；".join(str(suggestion).strip("。") for suggestion in notes if str(suggestion).strip())
        options["production_notes"] = "；".join(part for part in [existing_notes, repair_notes] if part)
    return options


def _choice(value: str, choices: set[str], fallback: str) -> str:
    return value if value in choices else fallback


def _parse_path_lines(input_value: str) -> list[Path]:
    paths: list[Path] = []
    for line in input_value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        paths.append(Path(stripped).expanduser())
    return paths


def _source_name_from_url(source_url: str) -> str:
    parsed = urlparse(str(source_url).strip())
    return parsed.netloc.lower() or str(source_url).strip()[:60] or "视频链接"


def _uploaded_file_items(form: cgi.FieldStorage) -> list:
    if "file" not in form:
        return []
    value = form["file"]
    items = value if isinstance(value, list) else [value]
    return [item for item in items if getattr(item, "filename", "")]


def build_quality_summary(artifact_paths: dict[str, str], options: dict | None = None) -> dict:
    quality_report = _read_json_artifact(artifact_paths.get("quality_report", ""))
    creative_plan = _read_json_artifact(artifact_paths.get("creative_plan", ""))
    original_strategy = _read_json_artifact(artifact_paths.get("original_strategy", ""))
    storyboard = _read_json_artifact(artifact_paths.get("storyboard", ""))
    reference_blueprint = _read_json_artifact(artifact_paths.get("reference_blueprint", ""))
    content_plan = _read_json_artifact(artifact_paths.get("content_plan", ""))
    storyboard_v2 = _read_json_artifact(artifact_paths.get("storyboard_v2", ""))
    visual_requirements = _read_json_artifact(artifact_paths.get("visual_requirements", ""))
    asset_sourcing_plan = _read_json_artifact(artifact_paths.get("asset_sourcing_plan", ""))
    generated_asset_manifest = _read_json_artifact(artifact_paths.get("generated_asset_manifest", ""))
    visual_insert_plan = _read_json_artifact(artifact_paths.get("visual_insert_plan", ""))
    generated_visual_manifest = _read_json_artifact(artifact_paths.get("generated_visual_manifest", ""))
    cover_asset_manifest = _read_json_artifact(artifact_paths.get("cover_asset_manifest", ""))
    voiceover_manifest = _read_json_artifact(artifact_paths.get("voiceover_manifest", ""))
    user_delivery = _read_json_artifact(artifact_paths.get("user_delivery", ""))
    originality_report = _read_json_artifact(artifact_paths.get("originality_report", ""))
    checks = quality_report.get("checks", {}) if isinstance(quality_report.get("checks"), dict) else {}
    issues = quality_report.get("issues", []) if isinstance(quality_report.get("issues"), list) else []
    variant = creative_plan.get("recommended_variant", {})
    segments = variant.get("segments", []) if isinstance(variant.get("segments"), list) else []
    template_like_count = sum(1 for segment in segments if segment.get("template_like"))
    template_like_limit = max(1, math.floor(len(segments) * 0.18)) if segments else 0
    strategy = creative_plan.get("creative_strategy", {})

    output_geometry = quality_report.get("output_geometry", {})
    source_geometry = quality_report.get("source_geometry", {})
    output_size = ""
    if output_geometry.get("width") and output_geometry.get("height"):
        output_size = f"{output_geometry['width']}x{output_geometry['height']}"
        if source_geometry.get("width") and source_geometry.get("height"):
            output_size += f" / 源片 {source_geometry['width']}x{source_geometry['height']}"

    metrics = []
    if variant.get("total_duration"):
        metrics.append({"label": "计划时长", "value": _format_duration(float(variant["total_duration"]))})
    if len(segments):
        metrics.append({"label": "创作片段", "value": f"{len(segments)} 段"})
    if template_like_limit:
        metrics.append({"label": "模板风险", "value": f"{template_like_count}/{template_like_limit}"})
    if output_size:
        metrics.append({"label": "画面比例", "value": output_size})
    report_strategy = quality_report.get("strategy", {}) if isinstance(quality_report.get("strategy"), dict) else {}
    if report_strategy.get("publish_tier"):
        tier_labels = {
            "publish_candidate": "发布候选",
            "preview_needs_asset_pass": "样片待资产升级",
            "preview_needs_asset_usage": "样片待素材入镜",
            "preview_needs_voiceover_upgrade": "样片待配音升级",
            "needs_assets": "需补素材",
            "needs_voiceover": "需补配音",
            "needs_rewrite": "内容需重写",
        }
        metrics.append(
            {
                "label": "发布分级",
                "value": tier_labels.get(str(report_strategy["publish_tier"]), str(report_strategy["publish_tier"])),
            }
        )
    if report_strategy.get("asset_pass_status"):
        metrics.append({"label": "资产通行证", "value": str(report_strategy["asset_pass_status"])})
    if report_strategy.get("asset_count") is not None:
        metrics.append({"label": "素材数量", "value": str(report_strategy["asset_count"])})
    if report_strategy.get("asset_usage_scene_count") is not None:
        metrics.append({"label": "资产入镜", "value": f"{report_strategy['asset_usage_scene_count']} 场"})
    if strategy.get("treatment"):
        metrics.append({"label": "导演方案", "value": str(strategy["treatment"])})
    if strategy.get("target_duration"):
        metrics.append({"label": "时长目标", "value": _format_duration(float(strategy["target_duration"]))})
    if original_strategy:
        metrics.append({"label": "生产类型", "value": "无参考原创"})
        if original_strategy.get("topic"):
            metrics.append({"label": "原创选题", "value": str(original_strategy["topic"])})
        if original_strategy.get("target_duration"):
            metrics.append({"label": "目标时长", "value": _format_duration(float(original_strategy["target_duration"]))})
    storyboard_scenes = storyboard.get("scenes", []) if isinstance(storyboard.get("scenes"), list) else []
    if storyboard_scenes:
        metrics.append({"label": "原创场景", "value": f"{len(storyboard_scenes)} 场"})
    if user_delivery:
        release_decision = user_delivery.get("release_decision", {})
        status_label = str(release_decision.get("status") or "待判断") if isinstance(release_decision, dict) else "待判断"
        metrics.append({"label": "交付结论", "value": status_label})
        if user_delivery.get("mode"):
            metrics.append({"label": "生产类型", "value": str(user_delivery["mode"])})
        front_labels = user_delivery.get("front_labels", {}) if isinstance(user_delivery.get("front_labels"), dict) else {}
        if front_labels.get("asset_status"):
            metrics.append({"label": "素材状态", "value": str(front_labels["asset_status"])})
        if front_labels.get("cover_status"):
            metrics.append({"label": "封面状态", "value": str(front_labels["cover_status"])})
        if front_labels.get("voice_status"):
            metrics.append({"label": "配音状态", "value": str(front_labels["voice_status"])})
    if reference_blueprint:
        metrics.append({"label": "参考用途", "value": "学习结构，不复用画面"})
    plan_chapters = content_plan.get("chapters", []) if isinstance(content_plan.get("chapters"), list) else []
    if plan_chapters:
        metrics.append({"label": "内容章节", "value": f"{len(plan_chapters)} 章"})
    storyboard_v2_scenes = storyboard_v2.get("scenes", []) if isinstance(storyboard_v2.get("scenes"), list) else []
    if storyboard_v2_scenes:
        metrics.append({"label": "原创分镜", "value": f"{len(storyboard_v2_scenes)} 场"})
    if visual_requirements:
        metrics.append({"label": "画面需求", "value": f"{int(visual_requirements.get('requirement_count') or 0)} 个"})
    if asset_sourcing_plan:
        metrics.append({"label": "画面策略", "value": str(asset_sourcing_plan.get("strategy") or "images2_first")})
    if visual_insert_plan:
        inserts = visual_insert_plan.get("inserts", []) if isinstance(visual_insert_plan.get("inserts"), list) else []
        total_insert_duration = float(visual_insert_plan.get("total_ai_insert_duration") or 0)
        max_ratio = float(visual_insert_plan.get("max_ai_insert_ratio") or 0.08)
        metrics.append({"label": "AI 补充镜头", "value": f"{len(inserts)} 个"})
        if total_insert_duration:
            metrics.append({"label": "AI 镜头时长", "value": _format_duration(total_insert_duration)})
        metrics.append({"label": "AI 镜头上限", "value": f"{int(max_ratio * 100)}%"})
    if generated_visual_manifest:
        visual_count = generated_visual_manifest.get("visual_count")
        if visual_count is None:
            visuals = generated_visual_manifest.get("visuals", [])
            visual_count = len(visuals) if isinstance(visuals, list) else None
        if visual_count is not None:
            metrics.append({"label": "生成补充图", "value": f"{visual_count} 张"})
        if generated_visual_manifest.get("publish_ready") is not None:
            metrics.append(
                {
                    "label": "AI 视觉状态",
                    "value": "可发布" if generated_visual_manifest.get("publish_ready") else "待 images2 升级",
                }
            )
    if generated_asset_manifest:
        asset_count = generated_asset_manifest.get("asset_count")
        if asset_count is not None:
            metrics.append({"label": "生成素材", "value": f"{asset_count} 个"})
        if generated_asset_manifest.get("publish_ready") is not None:
            metrics.append(
                {
                    "label": "生图门禁",
                    "value": "可发布" if generated_asset_manifest.get("publish_ready") else "待真实生图",
                }
            )
    if cover_asset_manifest:
        if cover_asset_manifest.get("provider"):
            metrics.append({"label": "封面来源", "value": str(cover_asset_manifest["provider"])})
        if cover_asset_manifest.get("publish_ready") is not None:
            metrics.append(
                {
                    "label": "封面门禁",
                    "value": "可发布" if cover_asset_manifest.get("publish_ready") else "待 images2 升级",
                }
            )
    if voiceover_manifest and not user_delivery:
        metrics.append({"label": "配音状态", "value": str(voiceover_manifest.get("front_label") or "待确认")})
    originality = _summarize_originality(originality_report)
    if originality:
        risk_label = str(originality.get("risk_level", "unknown"))
        score = originality.get("similarity_score")
        score_label = str(score) if score is not None else "--"
        metrics.append({"label": "原创风险", "value": f"{risk_label} / {score_label}"})
        originality_metrics = originality.get("metrics", {}) if isinstance(originality.get("metrics"), dict) else {}
        metrics.extend(
            [
                {"label": "相似度", "value": _format_percent(originality_metrics.get("visual_similarity"))},
                {"label": "音频复用", "value": _format_percent(originality_metrics.get("audio_reuse_ratio"))},
                {"label": "文本重合", "value": _format_percent(originality_metrics.get("text_overlap_ratio"))},
            ]
        )
    summary_issues = list(issues)
    if originality:
        originality_risk = str(originality.get("risk_level", "unknown"))
        if originality_risk == "high":
            summary_issues.append(
                {
                    "code": "originality_high_risk",
                    "severity": "blocker",
                    "message": "原创风险为 high，不能作为上线成片直接发布。",
                }
            )
        elif originality_risk == "medium":
            summary_issues.append(
                {
                    "code": "originality_medium_risk",
                    "severity": "error",
                    "message": "原创风险为 medium，上线前需要人工复核并继续降低同源比例。",
                }
            )
    if str((options or {}).get("visual_transform_policy", "")) == "cartoonize":
        summary_issues.append(
            {
                "code": "deprecated_cartoonize_output",
                "severity": "blocker",
                "message": "旧版卡通化/线稿化产物已被废弃，不能作为上线成片直接发布。",
            }
        )

    check_labels = {
        "preserve_source_geometry": "比例保持",
        "no_generated_cards_in_release": "无包装卡片",
        "creative_plan_template_like_budget": "模板预算",
        "longform_duration_floor": "时长底线",
        "creative_plan_has_director_moves": "导演动作",
        "creative_plan_has_release_chronology": "时间线",
        "creative_plan_has_content_evidence": "内容证据",
        "creative_plan_has_audio_evidence": "声音证据",
        "creative_plan_has_semantic_chapters": "语义章节",
        "visual_artifact_free": "真实画面",
        "original_script_present": "原创脚本",
        "storyboard_present": "原创分镜",
        "no_source_reuse": "无源片复用",
        "visual_preview_present": "原创样片",
        "originality_gate_passed": "原创闸门",
        "dynamic_shot_plan_present": "动态镜头",
        "caption_timeline_present": "字幕时间线",
        "subtitles_present": "字幕文件",
        "scene_motion_variety": "镜头变化",
        "ai_visual_insert_budget": "AI 镜头预算",
        "ai_visual_insert_non_consecutive": "AI 镜头间隔",
        "ai_visual_insert_relevance": "AI 镜头相关",
        "voiceover_audio_present": "旁白音频",
        "asset_pass_ready": "资产通行证",
        "asset_visuals_embedded": "资产入镜",
        "reference_blueprint_present": "参考蓝图",
        "reference_media_not_reused": "不搬运源片",
        "content_plan_depth": "内容深度",
        "storyboard_v2_present": "二代分镜",
        "visual_requirements_ready": "画面需求",
        "asset_sourcing_plan_images2_first": "生图优先",
        "visual_prompt_pack_present": "生图提示词",
        "generated_assets_ready": "素材生成",
        "generated_assets_publish_ready": "真实生图",
        "cover_brief_ready": "封面简报",
        "cover_prompt_pack_ready": "封面提示词",
        "cover_assets_ready": "封面生成",
        "cover_assets_publish_ready": "真实封面",
        "cover_not_overcomplicated": "封面简洁",
        "cover_text_concise": "封面文字",
        "duration_floor_respected": "时长保护",
        "subtitle_timeline_present": "自动字幕",
        "subtitle_readability": "字幕可读",
        "voice_provider_publishable": "可发布配音",
        "user_delivery_present": "用户交付",
        "rendered_release_present": "成片预览",
    }
    visible_checks = [
        {"label": label, "passed": bool(checks[key]), "key": key}
        for key, label in check_labels.items()
        if key in checks
    ]
    score_result = _score_quality(checks, summary_issues, options or {})

    return {
        "status": quality_report.get("status", "unknown") if quality_report else "unknown",
        "score": score_result["score"],
        "grade": score_result["grade"],
        "risk_level": score_result["risk_level"],
        "deductions": score_result["deductions"],
        "repair_suggestions": score_result["repair_suggestions"],
        "checks": visible_checks,
        "metrics": metrics,
        "originality": originality,
        "issues": [
            {
                "code": str(issue.get("code", "")),
                "severity": str(issue.get("severity", "error")),
                "message": str(issue.get("message", "")),
            }
            for issue in summary_issues[:5]
            if isinstance(issue, dict)
        ],
        "strategy": {
            "version": str(strategy.get("version", "")),
            "treatment": str(strategy.get("treatment", "")),
            "moves": [str(move) for move in strategy.get("creative_moves", [])[:6]]
            if isinstance(strategy.get("creative_moves"), list)
            else [],
        },
    }


def _score_quality(checks: dict, issues: list, options: dict) -> dict:
    strictness = str(options.get("quality_strictness", "standard"))
    failed_check_penalty = 14 if strictness == "audit" else 12 if strictness == "strict" else 10
    score = 100
    deductions: list[dict] = []
    for code, passed in checks.items():
        if passed:
            continue
        label = _repair_label_for_code(code)
        score -= failed_check_penalty
        deductions.append({"code": code, "points": failed_check_penalty, "message": label})
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "error"))
        if severity == "blocker":
            points = 45
        elif severity == "error":
            points = 15
        else:
            points = 6
        score -= points
        deductions.append(
            {
                "code": str(issue.get("code", "")),
                "points": points,
                "message": str(issue.get("message", "")) or _repair_label_for_code(str(issue.get("code", ""))),
            }
        )
    score = max(0, min(100, score))
    return {
        "score": score,
        "grade": _quality_grade(score),
        "risk_level": _risk_level(score),
        "deductions": deductions[:8],
        "repair_suggestions": _repair_suggestions(deductions),
    }


def _quality_grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 65:
        return "C"
    return "D"


def _risk_level(score: int) -> str:
    if score >= 90:
        return "low"
    if score >= 75:
        return "medium"
    return "high"


def _repair_label_for_code(code: str) -> str:
    if "originality" in code:
        return "提高原创内容比例：减少源片直接复用，加入原创解说、自有画面或重新录制素材。"
    if "geometry" in code:
        return "修复画面比例和分辨率，避免拉伸或裁切错误。"
    if "template" in code or "generated_cards" in code:
        return "降低模板感画面占比，禁止假片头片尾和包装卡片进入成片。"
    if "visual" in code or "artifact" in code:
        return "重做视觉方案，禁止过曝线稿、假脸贴片、卡通化遮掩等低质伪原创。"
    if "duration" in code or "longform" in code:
        return "提高长版覆盖线，避免把完整教程压成短摘要。"
    if "audio" in code:
        return "重新检查声音证据、响度和切点连续性。"
    if "asset" in code:
        return "补充自录/授权素材库和 asset_licenses.json 授权清单，再升级为发布候选。"
    if "semantic" in code or "chronology" in code:
        return "按语义章节重排正文，钩子后保持源片时间线推进。"
    if "content" in code or "transcript" in code:
        return "补强内容证据，优先使用字幕或 OCR 文字参与判断。"
    if "director" in code or "creative" in code:
        return "补足导演动作，让片段承担开场、语境、动作链、决策和收束。"
    return "打开 quality_report.json 查看问题，并用更严格参数返工。"


def _repair_suggestions(deductions: list[dict]) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()
    for deduction in deductions:
        message = _repair_label_for_code(str(deduction.get("code", "")))
        if message in seen:
            continue
        seen.add(message)
        suggestions.append(message)
    return suggestions[:5] or ["当前未发现阻断问题，可进入人工抽检。"]


def _read_json_artifact(path_value: str) -> dict:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _summarize_originality(report: dict) -> dict:
    if not report:
        return {}
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    return {
        "risk_level": str(report.get("risk_level", "unknown")),
        "risk_reason": str(report.get("risk_reason", "")),
        "similarity_score": report.get("similarity_score"),
        "metrics": {
            "visual_similarity": metrics.get("visual_similarity"),
            "audio_reuse_ratio": metrics.get("audio_reuse_ratio"),
            "audio_reuse_provider": metrics.get("audio_reuse_provider"),
            "text_overlap_ratio": metrics.get("text_overlap_ratio"),
            "text_overlap_provider": metrics.get("text_overlap_provider"),
            "source_reuse_ratio": metrics.get("source_reuse_ratio"),
            "duration_retention": metrics.get("duration_retention"),
        },
        "recommendations": [
            str(item)
            for item in report.get("recommendations", [])[:5]
            if isinstance(item, str)
        ]
        if isinstance(report.get("recommendations"), list)
        else [],
        "disclaimer": str(report.get("disclaimer", "")),
    }


def _format_duration(duration: float) -> str:
    seconds = max(0, int(round(duration)))
    minutes, remainder = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def _format_percent(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{round(float(value) * 100)}%"
    return "--"


def make_app_handler(job_store: JobStore):
    class WorkbenchHandler(BaseHTTPRequestHandler):
        server_version = "VideoFactoryWorkbench/1.0"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
                return
            if parsed.path == "/api/jobs":
                self._send_json({"jobs": job_store.list()})
                return
            if parsed.path.startswith("/api/jobs/"):
                job_id = unquote(parsed.path.rsplit("/", 1)[-1])
                job = job_store.get(job_id)
                if job is None:
                    self._send_json({"error": "任务不存在。"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(job)
                return
            if parsed.path.startswith("/artifact/"):
                self._send_artifact(parsed.path)
                return
            self._send_json({"error": "未找到。"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/repair"):
                job_id = unquote(parsed.path.split("/")[-2])
                self._create_repair_job(job_id)
                return
            if parsed.path != "/api/jobs":
                self._send_json({"error": "未找到。"}, HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length", "0"))
            parsed_request = parse_job_request(
                self.headers.get("Content-Type", ""),
                self.rfile,
                length,
                upload_dir=UPLOAD_ROOT,
            )
            if parsed_request.status != HTTPStatus.OK:
                self._send_json({"error": parsed_request.error}, parsed_request.status)
                return
            if parsed_request.mode == "original-generate":
                batch_id = time.strftime("batch-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
                job = job_store.create(
                    parsed_request.mode,
                    parsed_request.source_name or "无参考原创任务",
                    input_path="",
                    batch_id=batch_id,
                    options=parsed_request.options,
                )
                thread = threading.Thread(
                    target=_run_job,
                    args=(job_store, job["id"], None, parsed_request.mode),
                    daemon=True,
                )
                thread.start()
                payload = dict(job)
                payload.update({"batch_id": batch_id, "jobs": [job], "primary_job_id": job["id"]})
                self._send_json(payload, HTTPStatus.ACCEPTED)
                return
            if parsed_request.input_path is None and not parsed_request.source_urls:
                self._send_json(
                    {"error": parsed_request.error or "请提供视频文件、本地视频路径或视频链接。"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            missing_paths = [path for path in parsed_request.input_paths if not path.exists()]
            if missing_paths:
                self._send_json({"error": f"视频不存在：{missing_paths[0]}"}, HTTPStatus.BAD_REQUEST)
                return

            batch_id = time.strftime("batch-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            jobs = []
            for index, input_path in enumerate(parsed_request.input_paths):
                source_name = parsed_request.source_names[index] if index < len(parsed_request.source_names) else input_path.name
                job = job_store.create(
                    parsed_request.mode,
                    source_name,
                    input_path=str(input_path),
                    batch_id=batch_id,
                    options=parsed_request.options,
                )
                jobs.append(job)
                thread = threading.Thread(
                    target=_run_job,
                    args=(job_store, job["id"], input_path, parsed_request.mode),
                    daemon=True,
                )
                thread.start()
            offset = len(parsed_request.input_paths)
            for index, source_url in enumerate(parsed_request.source_urls):
                source_name_index = offset + index
                source_name = (
                    parsed_request.source_names[source_name_index]
                    if source_name_index < len(parsed_request.source_names)
                    else _source_name_from_url(source_url)
                )
                options = dict(parsed_request.options)
                options["source_url"] = source_url
                job = job_store.create(
                    parsed_request.mode,
                    source_name,
                    input_path="",
                    batch_id=batch_id,
                    options=options,
                )
                jobs.append(job)
                thread = threading.Thread(
                    target=_run_job,
                    args=(job_store, job["id"], None, parsed_request.mode),
                    daemon=True,
                )
                thread.start()
            if not jobs:
                self._send_json({"error": "请提供视频文件、本地视频路径或视频链接。"}, HTTPStatus.BAD_REQUEST)
                return
            payload = dict(jobs[0])
            payload.update({"batch_id": batch_id, "jobs": jobs, "primary_job_id": jobs[0]["id"]})
            self._send_json(payload, HTTPStatus.ACCEPTED)

        def log_message(self, format: str, *args) -> None:
            print(f"[workbench] {self.address_string()} - {format % args}")

        def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _create_repair_job(self, job_id: str) -> None:
            repair = job_store.create_repair(job_id)
            if repair is None:
                self._send_json({"error": "任务不存在。"}, HTTPStatus.NOT_FOUND)
                return
            input_path = Path(str(repair.get("input_path", "")))
            if not input_path.exists():
                self._send_json({"error": f"视频不存在：{input_path}"}, HTTPStatus.BAD_REQUEST)
                return
            thread = threading.Thread(
                target=_run_job,
                args=(job_store, repair["id"], input_path, str(repair.get("mode", "auto"))),
                daemon=True,
            )
            thread.start()
            self._send_json(repair, HTTPStatus.ACCEPTED)

        def _send_artifact(self, path: str) -> None:
            parts = [unquote(part) for part in path.split("/") if part]
            if len(parts) != 3:
                self._send_json({"error": "产物链接无效。"}, HTTPStatus.BAD_REQUEST)
                return
            _, job_id, name = parts
            job = job_store.get(job_id)
            if job is None:
                self._send_json({"error": "任务不存在。"}, HTTPStatus.NOT_FOUND)
                return
            artifact_path = Path(job.get("artifacts", {}).get(name, ""))
            if not artifact_path.exists():
                self._send_json({"error": "产物不存在。"}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(artifact_path))[0] or "application/octet-stream"
            data = artifact_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return WorkbenchHandler


def run_server(port: int = 56080) -> None:
    WORKBENCH_ROOT.mkdir(parents=True, exist_ok=True)
    store = JobStore(history_path=WORKBENCH_ROOT / "job_history.json")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_app_handler(store))
    print(f"Video Factory Workbench: http://127.0.0.1:{port}/")
    server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Start the local Video Factory workbench.")
    parser.add_argument("--port", type=int, default=56080)
    args = parser.parse_args(argv)
    run_server(port=args.port)


def _quality_summary_needs_auto_repair(summary: dict, options: dict) -> bool:
    if options.get("auto_repair_attempted"):
        return False
    if str(options.get("workflow", "")) != "reference_guided_original":
        return False
    if not summary:
        return False
    if str(summary.get("status", "")) == "passed" and int(summary.get("score") or 0) >= 80:
        return False
    issue_codes = {
        str(issue.get("code", ""))
        for issue in summary.get("issues", [])
        if isinstance(issue, dict)
    }
    deduction_codes = {
        str(deduction.get("code", ""))
        for deduction in summary.get("deductions", [])
        if isinstance(deduction, dict)
    }
    codes = issue_codes | deduction_codes
    repairable_keywords = (
        "asset",
        "generated",
        "cover",
        "voice",
        "duration",
        "subtitle",
        "visual_requirements",
        "prompt_pack",
    )
    return bool(codes) and any(any(keyword in code for keyword in repairable_keywords) for code in codes)


def _auto_repair_options(options: dict, summary: dict) -> dict:
    repaired = dict(options)
    codes = [
        str(item.get("code", ""))
        for item in list(summary.get("issues", [])) + list(summary.get("deductions", []))
        if isinstance(item, dict) and str(item.get("code", "")).strip()
    ]
    repaired["auto_repair_attempted"] = True
    repaired["auto_repair_reason_codes"] = codes[:8]
    repaired["quality_strictness"] = "audit"
    repaired["creative_strength"] = "strong"
    repaired["visual_asset_strategy"] = "images2_first"
    repaired["image_provider"] = "mock_images2"
    repaired["voice_provider"] = "mock_professional_voice"
    repaired["audio_policy"] = "replace_later"
    repaired["target_duration_policy"] = "source_guided"
    repaired.pop("target_duration_seconds", None)
    return repaired


def _write_auto_repair_report(path: Path, before_summary: dict, repair_options: dict) -> None:
    payload = {
        "version": "auto_repair_report_v1",
        "workflow": "reference_guided_original",
        "reason": "首版质检未达标，系统已自动返工一版。",
        "before": {
            "status": before_summary.get("status"),
            "score": before_summary.get("score"),
            "grade": before_summary.get("grade"),
            "risk_level": before_summary.get("risk_level"),
            "issues": before_summary.get("issues", []),
            "deductions": before_summary.get("deductions", []),
        },
        "repair_options": {
            "image_provider": repair_options.get("image_provider"),
            "voice_provider": repair_options.get("voice_provider"),
            "target_duration_policy": repair_options.get("target_duration_policy"),
            "visual_asset_strategy": repair_options.get("visual_asset_strategy"),
            "reason_codes": repair_options.get("auto_repair_reason_codes", []),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_job(store: JobStore, job_id: str, input_path: Path | None, mode: str) -> None:
    output_dir = WORKBENCH_ROOT / job_id
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        store.update(job_id, status="running")
        job = store.get(job_id) or {}
        options = dict(job.get("options", {}))
        source_name = str(job.get("source_name", "")).strip()
        if source_name and not options.get("source_name"):
            options["source_name"] = source_name
        source_artifacts: dict[str, str] = {}

        def progress(message: str) -> None:
            store.log(job_id, message)

        source_url = str(options.get("source_url", "")).strip()
        if input_path is None and source_url and mode != "original-generate":
            progress("解析视频链接")
            download_result = download_source_video(source_url, output_dir, progress=progress, title_context=options)
            input_path = download_result.video_path
            source_metadata = _source_metadata_from_download(download_result, source_url)
            options["source_title"] = source_metadata.get("source_title", "")
            options["source_platform"] = source_metadata.get("platform", "")
            options["recommended_publish_title"] = source_metadata.get("recommended_publish_title", "")
            store.update(job_id, input_path=str(input_path), source_metadata=source_metadata, options=options)
            source_artifacts = {
                "source_video": str(download_result.video_path),
                "source_download": str(download_result.report_path),
            }

        if mode == "reference-guided-original":
            if input_path is None:
                raise ValueError("缺少参考视频路径。")
            if not options.get("source_title"):
                options["source_title"] = input_path.stem or source_name or "参考视频原创解读"
                store.update(job_id, options=options)
            artifacts = render_reference_guided_original_video(
                reference_video_path=input_path,
                output_dir=output_dir,
                options=options,
                progress=progress,
            )
            preliminary_paths = {key: str(value) for key, value in artifacts.items() if key != "mode"}
            preliminary_paths.update(source_artifacts)
            preliminary_summary = build_quality_summary(preliminary_paths, options=options)
            if _quality_summary_needs_auto_repair(preliminary_summary, options):
                progress("质检未达标，自动返工一版")
                repair_options = _auto_repair_options(options, preliminary_summary)
                auto_repair_report = output_dir / "auto_repair_report.json"
                _write_auto_repair_report(auto_repair_report, preliminary_summary, repair_options)
                options = repair_options
                store.update(job_id, options=options)
                artifacts = render_reference_guided_original_video(
                    reference_video_path=input_path,
                    output_dir=output_dir,
                    options=options,
                    progress=progress,
                )
                source_artifacts["auto_repair_report"] = str(auto_repair_report)
        elif mode == "original-generate":
            artifacts = render_original_video(
                output_dir=output_dir,
                options=options,
                progress=progress,
            )
        else:
            if input_path is None:
                raise ValueError("缺少参考视频路径。")
            artifacts = render_replicate(
                input_path,
                mode=mode,
                output_dir=output_dir,
                progress=progress,
                production_options=options,
            )
        artifact_paths = {key: str(value) for key, value in artifacts.items() if key != "mode"}
        artifact_paths.update(source_artifacts)
        if mode != "original-generate":
            originality_report_path = output_dir / "originality_report.json"
            try:
                progress("生成原创度风控报告")
                edl_value = artifact_paths.get("edl", "")
                edl_path = Path(edl_value) if edl_value else None
                if input_path is None:
                    raise ValueError("缺少参考视频路径。")
                build_originality_report(
                    input_path,
                    Path(artifact_paths["video"]),
                    originality_report_path,
                    edl_path=edl_path if edl_path and edl_path.exists() else None,
                )
            except Exception as originality_error:  # pragma: no cover - depends on local ffmpeg/media shape.
                store.log(job_id, f"原创度报告生成失败：{originality_error}")
                _write_originality_unknown_report(originality_report_path, str(originality_error))
            artifact_paths["originality_report"] = str(originality_report_path)
        artifact_urls = {
            key: f"/artifact/{job_id}/{key}"
            for key in artifact_paths
            if key in {
                "video",
                "cover",
                "contact_sheet",
                "report",
                "quality_report",
                "edl",
                "creative_brief",
                "creative_plan",
                "candidate_edl",
                "cover_candidates",
                "content_analysis",
                "audio_analysis",
                "semantic_timeline",
                "transcript_analysis",
                "originality_report",
                "script",
                "storyboard",
                "original_strategy",
                "motion_plan",
                "caption_timeline",
                "subtitles",
                "voiceover_manifest",
                "asset_pass_report",
                "asset_usage_plan",
                "shotlist",
                "asset_manifest",
                "reference_blueprint",
                "content_plan",
                "script_v2",
                "storyboard_v2",
                "visual_requirements",
                "asset_sourcing_plan",
                "visual_insert_plan",
                "images2_prompt_pack",
                "generated_visual_manifest",
                "cover_brief",
                "cover_prompt_pack",
                "cover_asset_manifest",
                "visual_prompt_pack",
                "generated_asset_manifest",
                "user_delivery",
                "auto_repair_report",
                "source_video",
                "source_download",
            }
        }
        store.update(
            job_id,
            status="done",
            mode=str(artifacts.get("mode", mode)),
            artifacts=artifact_paths,
            artifact_urls=artifact_urls,
            quality_summary=build_quality_summary(artifact_paths, options=options),
        )
    except Exception as exc:  # pragma: no cover - exercised through manual server use.
        store.log(job_id, "任务失败")
        artifact_paths = {}
        source_video = output_dir / "source" / "source.mp4"
        if source_video.exists():
            artifact_paths["source_video"] = str(source_video)
        source_download = output_dir / "source_download.json"
        if source_download.exists():
            artifact_paths["source_download"] = str(source_download)
        quality_report = output_dir / "quality_report.json"
        if quality_report.exists():
            artifact_paths["quality_report"] = str(quality_report)
        originality_report = output_dir / "originality_report.json"
        if originality_report.exists():
            artifact_paths["originality_report"] = str(originality_report)
        artifact_urls = {key: f"/artifact/{job_id}/{key}" for key in artifact_paths}
        store.update(
            job_id,
            status="error",
            error=str(exc),
            artifacts=artifact_paths,
            artifact_urls=artifact_urls,
            quality_summary=build_quality_summary(artifact_paths, options=dict((store.get(job_id) or {}).get("options", {}))),
        )


def _source_metadata_from_download(download_result, source_url: str) -> dict:
    candidates = getattr(download_result, "publish_title_candidates", ()) or ()
    return {
        "source_url": source_url,
        "platform": str(getattr(download_result, "platform", "")),
        "source_title": str(getattr(download_result, "title", "")),
        "recommended_publish_title": str(getattr(download_result, "recommended_publish_title", "")),
        "publish_title_candidates": [str(candidate) for candidate in candidates if str(candidate).strip()],
    }


def _write_originality_unknown_report(output_path: Path, error: str) -> None:
    payload = {
        "risk_level": "unknown",
        "risk_reason": "原创度估算未完成，需要人工复核。",
        "similarity_score": None,
        "metrics": {
            "visual_similarity": None,
            "audio_reuse_ratio": None,
            "text_overlap_ratio": None,
            "source_reuse_ratio": None,
            "duration_retention": None,
        },
        "recommendations": ["原创度报告生成失败，上线前请人工确认素材授权、源片复用比例和解说文本重合。"],
        "error": error,
        "disclaimer": "本报告是本地可解释原创度估算，不代表任何平台的真实审核结果，也不用于规避平台检测。",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_upload_name(filename: str) -> str:
    name = Path(filename).name.replace("/", "-").replace("\\", "-")
    return name or "uploaded-video.mp4"


if __name__ == "__main__":
    main()
