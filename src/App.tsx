/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Film,
  FolderOpen,
  Play,
  Sliders,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';

type Stage = 'inputs' | 'cuts' | 'scenes' | 'motion' | 'export';
type MotionType = 'zoom_in' | 'zoom_out' | 'left' | 'right';
type ThemePreset = 'blue_night' | 'calm_forest' | 'daylight';

interface CutSegment {
  id: number;
  text: string;
  matchedText: string;
  start: number;
  end: number;
  confidence: number;
  estimated?: boolean;
  sceneType: 'image' | 'video';
  sceneFile: string;
  videoDuration?: number;
  color: string;
}

interface MotionAssignment {
  type: MotionType;
  x: number;
  y: number;
  strength: number;
  ease: boolean;
}

const THEME_OPTIONS: { id: ThemePreset; label: string }[] = [
  { id: 'blue_night', label: 'ليل أزرق' },
  { id: 'calm_forest', label: 'غابة هادية' },
  { id: 'daylight', label: 'نهار' },
];

const MOTION_TABS: { id: MotionType; label: string; icon: string }[] = [
  { id: 'zoom_in', label: 'Zoom In', icon: '⊕' },
  { id: 'zoom_out', label: 'Zoom Out', icon: '⊖' },
  { id: 'left', label: 'Left', icon: '⇠' },
  { id: 'right', label: 'Right', icon: '⇢' },
];

const INITIAL_SCRIPT = `1. في قلب الأندلس بدأت قصة حضارة مضيئة
2. كانت قرطبة مركزاً للعلم والعمارة والفنون
3. وتدفقت المياه في حدائق قصر الحمراء
4. وازدهرت المكتبات والمدارس في كل مدينة
5. وبقي أثر تلك الحقبة خالداً حتى يومنا هذا`;

const INITIAL_SEGMENTS: CutSegment[] = [
  {
    id: 1,
    text: 'في قلب الأندلس بدأت قصة حضارة مضيئة',
    matchedText: 'في قلب الأندلس بدات قصه',
    start: 0.0,
    end: 2.1,
    confidence: 96,
    sceneType: 'image',
    sceneFile: '1.png',
    color: 'linear-gradient(135deg, #2b3a55 0%, #1d2638 100%)',
  },
  {
    id: 2,
    text: 'كانت قرطبة مركزاً للعلم والعمارة والفنون',
    matchedText: 'كانت قرطبه مركزا للعلم',
    start: 2.1,
    end: 4.3,
    confidence: 92,
    sceneType: 'image',
    sceneFile: '02 - قرطبة.png',
    color: 'linear-gradient(135deg, #1f4037 0%, #182b26 100%)',
  },
  {
    id: 3,
    text: 'وتدفقت المياه في حدائق قصر الحمراء',
    matchedText: 'وتدفقت المياه في حدائق',
    start: 4.3,
    end: 6.8,
    confidence: 88,
    estimated: true,
    sceneType: 'video',
    sceneFile: 'scene_3.mp4',
    videoDuration: 0.8,
    color: 'linear-gradient(135deg, #3b2d54 0%, #201a2e 100%)',
  },
  {
    id: 4,
    text: 'وازدهرت المكتبات والمدارس في كل مدينة',
    matchedText: 'وازدهرت المكتبات والمدارس',
    start: 6.8,
    end: 8.6,
    confidence: 61,
    sceneType: 'image',
    sceneFile: '4.png',
    color: 'linear-gradient(135deg, #4b3a24 0%, #261e14 100%)',
  },
  {
    id: 5,
    text: 'وبقي أثر تلك الحقبة خالداً حتى يومنا هذا',
    matchedText: 'وبقي اثر تلك الحقبه',
    start: 8.6,
    end: 10.5,
    confidence: 94,
    sceneType: 'image',
    sceneFile: '5.png',
    color: 'linear-gradient(135deg, #253b4a 0%, #16222b 100%)',
  },
];

function fmtTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = (sec - m * 60).toFixed(1).padStart(4, '0');
  return `${String(m).padStart(2, '0')}:${s}`;
}

export default function App() {
  const [theme, setTheme] = useState<ThemePreset>('blue_night');
  const [projectName, setProjectName] = useState('وثائقي الحضارة الأندلسية');
  const [stage, setStage] = useState<Stage>('inputs');
  const [rawText, setRawText] = useState(INITIAL_SCRIPT);
  const [segments, setSegments] = useState<CutSegment[]>(INITIAL_SEGMENTS);
  const [motionTab, setMotionTab] = useState<MotionType>('zoom_in');
  const [globalStrength, setGlobalStrength] = useState(15);
  const [assignments, setAssignments] = useState<Record<number, MotionAssignment>>({
    1: { type: 'zoom_in', x: 0.36, y: 0.42, strength: 15, ease: true },
  });
  const [currentImageId, setCurrentImageId] = useState<number>(1);
  const [liveProgress, setLiveProgress] = useState<number | null>(null);
  const [liveStatus, setLiveStatus] = useState('');
  const [showInstallModal, setShowInstallModal] = useState(false);

  const parsedCount = useMemo(() => {
    return rawText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => /^\[?[0-9٠-٩]+/.test(l)).length;
  }, [rawText]);

  const imageSegments = useMemo(
    () => segments.filter((s) => s.sceneType === 'image'),
    [segments]
  );

  const tabIndex = (t: MotionType) => MOTION_TABS.findIndex((x) => x.id === t);

  const eligibleForTab = useMemo(() => {
    const currTIdx = tabIndex(motionTab);
    return imageSegments.filter((img) => {
      const a = assignments[img.id];
      if (!a) return true;
      return tabIndex(a.type) >= currTIdx;
    });
  }, [imageSegments, assignments, motionTab]);

  const activeImage = useMemo(() => {
    return (
      eligibleForTab.find((x) => x.id === currentImageId) ||
      eligibleForTab[0] ||
      null
    );
  }, [eligibleForTab, currentImageId]);

  const activeIdx = activeImage
    ? eligibleForTab.findIndex((x) => x.id === activeImage.id)
    : -1;

  const nudgeStart = (id: number, delta: number) => {
    setSegments((prev) => {
      const idx = prev.findIndex((s) => s.id === id);
      if (idx <= 0) return prev;
      const copy = prev.map((s) => ({ ...s }));
      const minT = copy[idx - 1].start + 0.2;
      const maxT = copy[idx].end - 0.2;
      const nextStart = Math.round(Math.max(minT, Math.min(maxT, copy[idx].start + delta)) * 10) / 10;
      copy[idx].start = nextStart;
      copy[idx - 1].end = nextStart;
      return copy;
    });
  };

  const runAutoPreview = () => {
    setLiveProgress(25);
    setLiveStatus('جاري مطابقة النص المرقّم مع ملف التفريغ المستورد...');
    setTimeout(() => {
      setLiveProgress(70);
      setLiveStatus('تم بناء نقاط القص وتركيب المشاهد على الخط الزمني...');
    }, 300);
    setTimeout(() => {
      setLiveProgress(100);
      setLiveStatus('جاهز لتحديد حركات الصور!');
      setTimeout(() => {
        setLiveProgress(null);
        setStage('motion');
      }, 350);
    }, 650);
  };

  const handleStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!activeImage) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));
    setAssignments((prev) => ({
      ...prev,
      [activeImage.id]: {
        type: motionTab,
        x: Number(x.toFixed(3)),
        y: Number(y.toFixed(3)),
        strength: prev[activeImage.id]?.strength ?? globalStrength,
        ease: prev[activeImage.id]?.ease ?? true,
      },
    }));
  };

  const selectTheme = (next: ThemePreset) => {
    setTheme(next);
    document.documentElement.setAttribute('data-theme', next);
  };

  const stages: { id: Stage; num: number; label: string }[] = [
    { id: 'inputs', num: 1, label: 'المدخلات' },
    { id: 'cuts', num: 2, label: 'التفريغ (استيراد)' },
    { id: 'scenes', num: 3, label: 'المشاهد' },
    { id: 'motion', num: 4, label: 'الحركات' },
    { id: 'export', num: 5, label: 'التصدير' },
  ];

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ background: 'var(--bg-app)', color: 'var(--text-primary)' }}
    >
      {/* Top Bar */}
      <header
        className="sticky top-0 z-30 h-15 px-6 flex items-center justify-between gap-4 border-b"
        style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
      >
        <div className="flex items-center gap-3 whitespace-nowrap">
          <span
            className="w-8 h-8 rounded-lg flex items-center justify-center border"
            style={{
              background: 'rgba(108,155,255,0.14)',
              borderColor: 'var(--border)',
              color: 'var(--accent)',
            }}
          >
            <Film className="w-4 h-4" />
          </span>
          <span className="text-lg font-bold">أوتوكَت · AutoCut</span>
          <span
            className="text-xs font-mono px-2 py-0.5 rounded border"
            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            v1.2.0
          </span>
        </div>

        <div className="hidden md:flex items-center gap-3 text-sm">
          <span className="font-semibold">{projectName}</span>
          <span className="text-xs font-semibold" style={{ color: 'var(--success-text)' }}>
            تم الحفظ ✓
          </span>
        </div>

        <div className="flex items-center gap-2">
          <div
            className="inline-flex items-center p-0.5 rounded-lg border gap-0.5"
            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
            role="group"
            aria-label="المظهر"
          >
            {THEME_OPTIONS.map((t) => {
              const active = theme === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => selectTheme(t.id)}
                  className="px-2.5 py-1 rounded-md text-xs font-semibold cursor-pointer transition-colors"
                  style={{
                    background: active ? 'var(--accent-btn)' : 'transparent',
                    color: active ? 'var(--on-accent)' : 'var(--text-muted)',
                  }}
                >
                  {t.label}
                </button>
              );
            })}
          </div>
          <div
            className="hidden sm:flex px-3 py-1.5 rounded-lg text-xs font-medium border items-center gap-1.5"
            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          >
            <Sliders className="w-3.5 h-3.5" />
            1920×1080 · 30fps
          </div>
        </div>
      </header>

      {/* Stepper */}
      <nav
        className="px-6 py-2.5 border-b overflow-x-auto"
        style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
      >
        <div className="max-w-7xl mx-auto flex items-center gap-2">
          {stages.map((st, idx) => {
            const active = stage === st.id;
            const done = stages.findIndex((x) => x.id === stage) > idx;
            return (
              <React.Fragment key={st.id}>
                <button
                  onClick={() => setStage(st.id)}
                  className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-sm font-medium border transition-colors cursor-pointer whitespace-nowrap"
                  style={{
                    background: active ? 'rgba(108,155,255,0.14)' : 'transparent',
                    borderColor: active ? 'var(--border)' : 'transparent',
                    color: active ? 'var(--accent)' : done ? 'var(--text-primary)' : 'var(--text-muted)',
                  }}
                >
                  <span
                    className="w-5 h-5 rounded text-xs font-bold inline-flex items-center justify-center tabular-nums"
                    style={{
                      background: active ? 'var(--accent-btn)' : done ? 'rgba(87,211,155,0.16)' : 'var(--bg-raised)',
                      color: active ? 'var(--on-accent)' : done ? 'var(--success-text)' : 'var(--text-muted)',
                    }}
                  >
                    {done ? '✓' : st.num}
                  </span>
                  {st.label}
                </button>
                {idx < stages.length - 1 && (
                  <span style={{ color: 'var(--border)' }}>←</span>
                )}
              </React.Fragment>
            );
          })}
        </div>
      </nav>

      {/* Main Content */}
      <main className="flex-1 w-full max-w-7xl mx-auto p-6">
        {/* Windows Local Package Banner + Download ZIP Button */}
        <div
          className="mb-5 px-4 py-3.5 rounded-xl border flex flex-wrap items-center justify-between gap-3 text-xs"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        >
          <div className="flex items-center gap-2.5">
            <Terminal className="w-4 h-4 shrink-0" style={{ color: 'var(--accent)' }} />
            <div>
              <div className="font-semibold text-sm">
                تشغيل AutoCut v1.2.0 محلياً على جهازك (Windows 10/11 — مجاني 100%)
              </div>
              <div className="mt-0.5" style={{ color: 'var(--text-muted)' }}>
                استيراد خارجي للتفريغ من <span dir="ltr" className="font-mono">Vibe</span> (<span dir="ltr" className="font-mono">JSON / SRT / VTT</span>) + <span dir="ltr" className="font-mono">FFmpeg</span> + <span dir="ltr" className="font-mono">FastAPI</span> — نزّل الحزمة وشغّل <span dir="ltr" className="font-mono">install.bat</span> ثم <span dir="ltr" className="font-mono">run.bat</span>.
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setShowInstallModal(true)}
              className="px-3 py-2 rounded-lg border text-xs font-semibold cursor-pointer"
              style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
            >
              خطوات التثبيت والربط
            </button>
            <a
              href="/autocut-windows.zip"
              download="autocut-windows.zip"
              className="px-4 py-2 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5"
              style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
            >
              <Download className="w-3.5 h-3.5" />
              تنزيل حزمة AutoCut (.zip)
            </a>
          </div>
        </div>

        {/* Live Automatic Banner */}
        {liveProgress !== null && (
          <div
            className="mb-6 p-4 rounded-xl border"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
          >
            <div className="flex items-center justify-between text-sm font-semibold mb-2">
              <span>{liveStatus}</span>
              <span className="tabular-nums">{liveProgress}%</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--bg-raised)' }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${liveProgress}%`, background: 'var(--accent)' }}
              />
            </div>
          </div>
        )}

        {/* Stage 1: New Project 2x2 Grid */}
        {stage === 'inputs' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">مشروع جديد</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  رتّب المواد الأربع هنا، أو أسقط مجلد المشروع دفعة واحدة ثم اضغط التشغيل.
                </p>
              </div>
              <button
                onClick={() => setStage('cuts')}
                className="px-4 py-2 rounded-xl border text-xs font-semibold cursor-pointer"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                فتح شاشة التفريغ (استيراد)
              </button>
            </div>

            {/* Top Row: Project Name + Quick Folder */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div
                className="p-4 rounded-xl border"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <label className="block text-xs font-semibold mb-1.5">اسم المشروع</label>
                <input
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg border text-sm"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                />
              </div>
              <div
                className="p-4 rounded-xl border border-dashed flex items-center justify-between gap-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div>
                  <div className="text-sm font-bold">مشروع سريع من فولدر</div>
                  <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    أسقط مجلد المشروع لتعبئة الصوت والتفريغ و<span dir="ltr" className="font-mono">script.txt</span> و<span dir="ltr" className="font-mono">scenes/</span> تلقائياً
                  </div>
                </div>
                <span
                  className="px-3 py-1.5 rounded-lg border text-xs font-semibold shrink-0"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                >
                  اختر مجلد
                </span>
              </div>
            </div>

            {/* 2x2 Materials Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* 1. Audio */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>1</span>
                    الصوت
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded border" style={{ color: 'var(--success-text)', borderColor: 'var(--success-text)' }}>
                    جاهز ✓
                  </span>
                </div>
                <div
                  className="p-3.5 rounded-xl border border-dashed text-center text-xs"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                >
                  <div className="font-semibold text-sm">narration_andalus.wav</div>
                  <div className="mt-1 tabular-nums" style={{ color: 'var(--text-muted)' }}>
                    المدة: 10.5 ث · يبقى الصوت أصلياً دون قص أو تعديل
                  </div>
                </div>
              </section>

              {/* 2. External Transcript */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>2</span>
                    التفريغ
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded border" style={{ color: 'var(--success-text)', borderColor: 'var(--success-text)' }}>
                    JSON — توقيت كل كلمة ✓
                  </span>
                </div>
                <div
                  className="p-3 rounded-lg border text-xs leading-5"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                >
                  <strong style={{ color: 'var(--text-primary)' }}>دليل التصدير من Vibe:</strong> افتح <span dir="ltr" className="font-mono">More Options</span> ← فعّل <span dir="ltr" className="font-mono">Word timestamps</span> ← صدّر بصيغة <span dir="ltr" className="font-mono">JSON</span> (يقبل أيضاً <span dir="ltr" className="font-mono">SRT / VTT</span>).
                </div>
                <div className="flex items-center justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
                  <span dir="ltr" className="font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>narration_andalus.json</span>
                  <span className="tabular-nums">28 كلمة · التغطية 98%</span>
                </div>
              </section>

              {/* 3. Numbered Script */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>3</span>
                    النص المرقّم
                  </div>
                  <span className="text-xs font-semibold tabular-nums" style={{ color: 'var(--accent)' }}>
                    {parsedCount} مقاطع
                  </span>
                </div>
                <textarea
                  value={rawText}
                  onChange={(e) => setRawText(e.target.value)}
                  rows={5}
                  className="w-full p-3 rounded-xl border text-sm leading-7"
                  style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                />
                <div className="flex items-center justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
                  <span className="inline-flex items-center gap-1"><FileText className="w-3.5 h-3.5" /> يدعم اللصق أو استيراد ملف <span dir="ltr" className="font-mono">.txt</span></span>
                  <span>1..N</span>
                </div>
              </section>

              {/* 4. Scenes */}
              <section
                className="p-5 rounded-xl border space-y-3"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-bold">
                    <span className="w-6 h-6 rounded-md text-xs flex items-center justify-center border" style={{ borderColor: 'var(--border)', background: 'var(--bg-raised)', color: 'var(--accent)' }}>4</span>
                    المشاهد
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-0.5 rounded border" style={{ color: 'var(--success-text)', borderColor: 'var(--success-text)' }}>
                    5 مشاهد جاهزة ✓
                  </span>
                </div>
                <div className="flex gap-2">
                  <input
                    readOnly
                    dir="ltr"
                    value="C:\Projects\Andalus\scenes"
                    className="flex-1 px-3 py-2 rounded-lg border text-xs font-mono"
                    style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                  />
                  <span
                    className="px-3 py-2 rounded-lg border text-xs flex items-center gap-1.5"
                    style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                  >
                    <FolderOpen className="w-3.5 h-3.5" />
                    4 صور · 1 فيديو
                  </span>
                </div>
              </section>
            </div>

            {/* Settings Summary + Footer CTA */}
            <div
              className="mt-4 px-4 py-3 rounded-xl border flex flex-wrap items-center justify-between gap-3 text-xs"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
            >
              <span>الإعدادات الحالية: <strong style={{ color: 'var(--text-primary)' }}>1920×1080 · 30 fps · قوة الحركة {globalStrength}% · تمويه الخلفية</strong></span>
              <span>مظهر: <strong style={{ color: 'var(--text-primary)' }}>{THEME_OPTIONS.find((x) => x.id === theme)?.label}</strong></span>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <button
                  onClick={runAutoPreview}
                  className="px-6 py-2.5 rounded-xl font-semibold cursor-pointer"
                  style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
                >
                  ابدأ التشغيل التلقائي
                </button>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  مفيش حاجة بتشتغل قبل ما تدوس
                </span>
              </div>
              <button
                onClick={() => setStage('cuts')}
                className="px-4 py-2 rounded-xl border text-xs font-semibold cursor-pointer"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                مطابقة ومراجعة القص أولاً
              </button>
            </div>
          </div>
        )}

        {/* Stage 2: External Transcript Import & Cuts Review */}
        {stage === 'cuts' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-5 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">2. التفريغ (استيراد) ونقاط القص</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  استورد ملف Vibe JSON أو SRT أو VTT؛ يتم تمييز المطابقات الوسطية في ملفات الجمل بشارة «تقدير».
                </p>
              </div>
              <button
                onClick={() => setStage('scenes')}
                className="px-5 py-2.5 rounded-xl font-semibold cursor-pointer"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                حفظ ومتابعة
              </button>
            </div>

            <div
              className="rounded-2xl border overflow-x-auto"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <table className="w-full text-sm text-right border-collapse">
                <thead>
                  <tr style={{ background: 'var(--bg-raised)', color: 'var(--text-muted)' }}>
                    <th className="p-3">#</th>
                    <th className="p-3">النص المدخل</th>
                    <th className="p-3">النص المطابق</th>
                    <th className="p-3">البداية</th>
                    <th className="p-3">النهاية</th>
                    <th className="p-3">المدة</th>
                    <th className="p-3">الدقة</th>
                  </tr>
                </thead>
                <tbody>
                  {segments.map((s, idx) => (
                    <tr
                      key={s.id}
                      className="border-t"
                      style={{
                        borderColor: 'var(--border)',
                        background: s.confidence < 70 ? 'rgba(244,184,96,0.08)' : 'transparent',
                      }}
                    >
                      <td className="p-3 font-bold tabular-nums">{s.id}</td>
                      <td className="p-3">{s.text}</td>
                      <td className="p-3" style={{ color: 'var(--text-muted)' }}>{s.matchedText}</td>
                      <td className="p-3">
                        <div className="inline-flex items-center gap-1" dir="ltr">
                          <button
                            disabled={idx === 0}
                            onClick={() => nudgeStart(s.id, -0.1)}
                            className="w-7 h-7 rounded border text-xs cursor-pointer disabled:opacity-40"
                            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                          >
                            -
                          </button>
                          <span className="w-14 text-center font-mono text-xs tabular-nums">
                            {s.start.toFixed(2)}
                          </span>
                          <button
                            disabled={idx === 0}
                            onClick={() => nudgeStart(s.id, 0.1)}
                            className="w-7 h-7 rounded border text-xs cursor-pointer disabled:opacity-40"
                            style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
                          >
                            +
                          </button>
                        </div>
                      </td>
                      <td className="p-3 font-mono text-xs tabular-nums">{fmtTime(s.end)}</td>
                      <td className="p-3 font-mono text-xs tabular-nums">{(s.end - s.start).toFixed(2)}s</td>
                      <td className="p-3">
                        <span
                          className="px-2.5 py-0.5 rounded-md text-xs font-semibold tabular-nums border"
                          style={{
                            borderColor: s.confidence >= 80 ? 'var(--success-text)' : 'var(--warning-text)',
                            color: s.confidence >= 80 ? 'var(--success-text)' : 'var(--warning-text)',
                          }}
                        >
                          {s.confidence}%
                        </span>
                        {s.estimated && (
                          <span
                            className="mr-1.5 px-2 py-0.5 rounded text-[11px] font-bold border"
                            style={{ borderColor: 'var(--warning-text)', color: 'var(--warning-text)' }}
                          >
                            تقدير
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Stage 3: Scenes */}
        {stage === 'scenes' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-5 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">3. توزيع المشاهد على الخط الزمني</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  خط زمني متصل بدون أي فراغات؛ الفيديوهات صامتة وتمتد لتطابق مدة المقطع بالكامل.
                </p>
              </div>
              <button
                onClick={() => setStage('motion')}
                className="px-5 py-2.5 rounded-xl font-semibold cursor-pointer"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                متابعة إلى الحركات
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
              {segments.map((s) => {
                const segDur = s.end - s.start;
                const speed = s.videoDuration ? s.videoDuration / segDur : 1;
                return (
                  <div
                    key={s.id}
                    className="rounded-xl border overflow-hidden"
                    style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                  >
                    <div
                      className="h-28 relative flex items-center justify-center text-white font-bold text-2xl tabular-nums"
                      style={{ background: s.color }}
                    >
                      #{s.id}
                    </div>
                    <div className="p-3 text-xs space-y-1">
                      <div className="flex justify-between">
                        <span>{s.sceneType === 'video' ? `فيديو · ${speed.toFixed(2)}x` : 'صورة'}</span>
                        <span className="font-mono tabular-nums">{segDur.toFixed(2)}s</span>
                      </div>
                      <div dir="ltr" className="truncate" style={{ color: 'var(--text-muted)' }}>
                        {s.sceneFile}
                      </div>
                      {s.sceneType === 'video' && speed < 0.4 && (
                        <div className="inline-block px-2 py-0.5 rounded font-semibold" style={{ color: 'var(--warning-text)' }}>
                          سرعة بطيئة جداً: {speed.toFixed(2)}x
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Stage 4: Motion Studio */}
        {stage === 'motion' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <div>
                <h1 className="text-2xl font-bold">4. حركات الصور (نقرة واحدة على نقطة التركيز)</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  انقر لوضع النقطة الزرقاء (<span dir="ltr" className="font-mono">#4C8DFF</span> ثابتة في كل المظاهر).
                </p>
              </div>
              <button
                onClick={() => setStage('export')}
                className="px-6 py-2.5 rounded-xl font-semibold cursor-pointer"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                تنفيذ الحركات والتصدير
              </button>
            </div>

            <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
              <div
                className="inline-flex p-1 rounded-xl border gap-1"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                {MOTION_TABS.map((t) => {
                  const active = motionTab === t.id;
                  const count = imageSegments.filter((i) => assignments[i.id]?.type === t.id).length;
                  return (
                    <button
                      key={t.id}
                      onClick={() => setMotionTab(t.id)}
                      className="px-3.5 py-1.5 rounded-lg text-xs font-semibold cursor-pointer flex items-center gap-1.5"
                      style={{
                        background: active ? 'var(--accent-btn)' : 'transparent',
                        color: active ? 'var(--on-accent)' : 'var(--text-muted)',
                      }}
                    >
                      <span>{t.icon}</span>
                      <span>{t.label}</span>
                      <span className="tabular-nums opacity-85">{count}</span>
                    </button>
                  );
                })}
              </div>

              <label className="flex items-center gap-2 text-xs">
                <span>قوة الحركة العامة:</span>
                <input
                  type="range"
                  min={5}
                  max={40}
                  value={globalStrength}
                  onChange={(e) => setGlobalStrength(Number(e.target.value))}
                />
                <span className="font-mono tabular-nums">{globalStrength}%</span>
              </label>
            </div>

            {activeImage ? (
              <div className="grid grid-cols-[48px_1fr_48px] items-center gap-3 mb-4">
                <button
                  disabled={activeIdx <= 0}
                  onClick={() => activeIdx > 0 && setCurrentImageId(eligibleForTab[activeIdx - 1].id)}
                  className="w-12 h-12 rounded-full border flex items-center justify-center cursor-pointer disabled:opacity-35"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <ChevronRight className="w-5 h-5" />
                </button>

                <div
                  className="rounded-2xl border p-4 flex flex-col items-center justify-center"
                  style={{ background: '#08090C', borderColor: 'var(--border)' }}
                >
                  <div className="w-full flex items-center justify-between text-xs mb-3 text-slate-300">
                    <span className="tabular-nums font-semibold">
                      صورة {activeIdx + 1} من {eligibleForTab.length} · مشهد #{activeImage.id}
                    </span>
                    {assignments[activeImage.id]?.type === motionTab && (
                      <button
                        onClick={() =>
                          setAssignments((prev) => {
                            const next = { ...prev };
                            delete next[activeImage.id];
                            return next;
                          })
                        }
                        className="px-2.5 py-1 rounded bg-red-500/20 text-red-300 flex items-center gap-1 cursor-pointer"
                      >
                        <Trash2 className="w-3 h-3" />
                        مسح النقطة
                      </button>
                    )}
                  </div>

                  <div
                    onClick={handleStageClick}
                    className="relative w-full max-w-xl aspect-video rounded-lg cursor-crosshair flex items-center justify-center text-white/90 text-xl font-bold select-none"
                    style={{ background: activeImage.color }}
                  >
                    <span>مشهد #{activeImage.id} — {activeImage.sceneFile}</span>
                    {assignments[activeImage.id]?.type === motionTab && (
                      <div
                        className="absolute w-6 h-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] border-white focus-dot-pulse pointer-events-none"
                        style={{
                          background: '#4C8DFF',
                          left: `${assignments[activeImage.id].x * 100}%`,
                          top: `${assignments[activeImage.id].y * 100}%`,
                        }}
                      />
                    )}
                  </div>
                </div>

                <button
                  disabled={activeIdx >= eligibleForTab.length - 1}
                  onClick={() =>
                    activeIdx < eligibleForTab.length - 1 &&
                    setCurrentImageId(eligibleForTab[activeIdx + 1].id)
                  }
                  className="w-12 h-12 rounded-full border flex items-center justify-center cursor-pointer disabled:opacity-35"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <ChevronLeft className="w-5 h-5" />
                </button>
              </div>
            ) : (
              <div
                className="p-8 rounded-2xl border border-dashed text-center mb-4"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                تم تعيين جميع الصور في التبويبات السابقة ✓
              </div>
            )}

            {/* Thumbnail Strip */}
            <div
              className="p-2.5 rounded-xl border flex gap-2 overflow-x-auto"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              {imageSegments.map((img) => {
                const a = assignments[img.id];
                const tMeta = a ? MOTION_TABS.find((t) => t.id === a.type) : null;
                const isSel = activeImage?.id === img.id;
                return (
                  <button
                    key={img.id}
                    onClick={() => {
                      if (a && tabIndex(a.type) < tabIndex(motionTab)) {
                        setMotionTab(a.type);
                      }
                      setCurrentImageId(img.id);
                    }}
                    className="relative w-24 h-14 rounded-lg shrink-0 border-2 overflow-hidden flex items-center justify-center text-white text-xs font-bold cursor-pointer"
                    style={{
                      background: img.color,
                      borderColor: isSel ? 'var(--accent)' : 'var(--border)',
                    }}
                  >
                    #{img.id} {tMeta ? tMeta.icon : ''}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Stage 5: Export Report */}
        {stage === 'export' && (
          <div>
            <div className="flex items-center justify-between gap-4 mb-5">
              <div>
                <h1 className="text-2xl font-bold">5. التصدير والتقرير النهائي</h1>
                <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                  تصدير متوازي H.264 مع ذاكرة مؤقتة لكل مشهد ومطابقة كاملة لطول الصوت الأصلي.
                </p>
              </div>
              <button
                onClick={() => setStage('motion')}
                className="px-4 py-2 rounded-xl border text-sm cursor-pointer"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                تعديل الحركات
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-5">
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>إجمالي المقاطع</div>
                <div className="text-2xl font-bold tabular-nums mt-1">{segments.length}</div>
              </div>
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>صور بحركة مخصصة</div>
                <div className="text-2xl font-bold tabular-nums mt-1">{Object.keys(assignments).length}</div>
              </div>
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>مدة الصوت والفيديو</div>
                <div className="text-2xl font-bold font-mono tabular-nums mt-1">10.5s</div>
              </div>
              <div className="p-4 rounded-xl border" style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}>
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>حالة التطابق</div>
                <div className="text-lg font-bold mt-1 flex items-center gap-1.5" style={{ color: 'var(--success-text)' }}>
                  <Check className="w-5 h-5" />
                  صفر فجوات
                </div>
              </div>
            </div>

            <div
              className="p-6 rounded-2xl border space-y-3 text-sm"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <div className="flex items-center gap-2 font-semibold text-base">
                <Play className="w-4 h-4" style={{ color: 'var(--accent)' }} />
                تشغيل المشروع محلياً على جهاز ويندوز
              </div>
              <p style={{ color: 'var(--text-muted)' }}>
                يتم التصدير الفعلي محلياً عبر <span dir="ltr" className="font-mono">FFmpeg</span> بعد استيراد ملف التفريغ من <span dir="ltr" className="font-mono">Vibe</span> (<span dir="ltr" className="font-mono">JSON / SRT / VTT</span>) لضمان العمل 100% بدون إنترنت وبأقصى سرعة على المعالج.
              </p>
            </div>
          </div>
        )}
      </main>

      {/* Windows Installation & Setup Guide Modal */}
      {showInstallModal && (
        <div
          className="fixed inset-0 z-50 bg-black/65 flex items-center justify-center p-4"
          onClick={() => setShowInstallModal(false)}
        >
          <div
            className="w-full max-w-lg rounded-2xl border p-6 space-y-4"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">تثبيت وتشغيل AutoCut v1.2.0 على جهازك</h3>
              <button
                onClick={() => setShowInstallModal(false)}
                className="p-1.5 rounded-lg border cursor-pointer"
                style={{ background: 'var(--bg-raised)', borderColor: 'var(--border)' }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <ol className="space-y-3 text-sm leading-6 list-decimal list-inside" style={{ color: 'var(--text-primary)' }}>
              <li>
                اضغط زر <strong>تنزيل حزمة AutoCut (.zip)</strong> ثم فك الضغط عنها في أي مجلد (مثلاً <span dir="ltr" className="font-mono">C:\autocut</span>).
              </li>
              <li>
                تأكد من وجود <strong>Python 3.11</strong> (أو أحدث) على ويندوز مع تفعيل خيار <span dir="ltr" className="font-mono">Add Python to PATH</span>.
              </li>
              <li>
                اضغط مرتين على <span dir="ltr" className="font-mono font-semibold">install.bat</span> لتثبيت <span dir="ltr" className="font-mono">FastAPI</span> و<span dir="ltr" className="font-mono">rapidfuzz</span> و<span dir="ltr" className="font-mono">OpenCV</span> و<span dir="ltr" className="font-mono">FFmpeg</span> (خفيف وسريع بدون حزم تفريغ ثقيلة).
              </li>
              <li>
                فرّغ صوتك خارجياً في برنامج <strong>Vibe</strong> (من <span dir="ltr" className="font-mono">More Options</span> ← فعّل <span dir="ltr" className="font-mono">Word timestamps</span> ← صدّر <span dir="ltr" className="font-mono">JSON</span>) ثم استورده في AutoCut أو أسقط مجلد المشروع دفعة واحدة.
              </li>
              <li>
                بعد التثبيت، اضغط مرتين على <span dir="ltr" className="font-mono font-semibold">run.bat</span> لفتح <span dir="ltr" className="font-mono">http://127.0.0.1:8000</span>، وللحصول على التحديثات المستقبلية استخدم <strong>التحديثات ← المصدر</strong> من GitHub بنقرة واحدة.
              </li>
            </ol>

            <div className="flex items-center justify-end gap-2 pt-2">
              <a
                href="/autocut-windows.zip"
                download="autocut-windows.zip"
                className="px-4 py-2 rounded-xl text-sm font-semibold inline-flex items-center gap-2"
                style={{ background: 'var(--accent-btn)', color: 'var(--on-accent)' }}
              >
                <Download className="w-4 h-4" />
                تنزيل الحزمة الآن (.zip)
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
