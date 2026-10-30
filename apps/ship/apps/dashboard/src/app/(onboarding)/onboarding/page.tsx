"use client";

import { Icon as UiIcon } from "@repo/ui/icons";
import { BRAND_LINKS } from "@repo/core";

import { useState, useCallback, useRef, Suspense } from "react";
import { useI18n } from "@/components/i18n-provider";
import type { OnboardingStep, OnboardingState } from "@repo/onboarding";
import { nextStep, prevStep } from "@repo/onboarding";
import { ChooseStep } from "./_components/choose-step";
import { SelfhostChoiceStep } from "./_components/selfhost-choice-step";
import { SshStep } from "./_components/ssh-step";
import { TunnelStep } from "./_components/tunnel-step";
import { PreferencesStep } from "./_components/preferences-step";
import { LoadingStep } from "./_components/loading-step";
import { useTheme } from "@/components/theme-provider";
import { ThemeIcon } from "@/components/theme-icon";
import { Logo } from "@/components/logo";
import { locales, isRtl, type Locale } from "@/i18n";
import "./onboarding.css";

/* ── SVG icons used in the top bar ── */

// Modified by Netie AI, 2026: English-only — see apps/dashboard/src/i18n/index.ts.
/** Each language's own name / short glyph, in its own script (never translated). */
const LANG_NATIVE: Record<Locale, string> = { en: "English" };
const LANG_CODE: Record<Locale, string> = { en: "EN" };

function OnboardingInner() {
  const { t, locale, setLocale } = useI18n();
  const { toggle } = useTheme();
  const [langOpen, setLangOpen] = useState(false);
  const [step, setStep] = useState<OnboardingStep>("choose");
  const [state, setState] = useState<OnboardingState>({
    buildMode: "auto",
    apiUrl: "",
    dashboardUrl: "",
  });

  // Ref keeps latest state so goNext/goBack never read stale closures
  const stateRef = useRef(state);

  const updateState = useCallback(
    (patch: Partial<OnboardingState>) => {
      stateRef.current = { ...stateRef.current, ...patch };
      setState(stateRef.current);
    },
    [],
  );

  const goNext = useCallback(() => {
    const next = nextStep(step, stateRef.current);
    if (next) setStep(next);
  }, [step]);

  const goBack = useCallback(() => {
    const prev = prevStep(step, stateRef.current);
    if (prev) setStep(prev);
  }, [step]);

  return (
    <>
      {/* Aurora background */}
      <div className="ob-aurora">
        <div className="ob-aurora-blob ob-aurora-core" />
        <div className="ob-aurora-blob ob-aurora-left" />
        <div className="ob-aurora-blob ob-aurora-right" />
      </div>

      {/* Top bar */}
      <div className="ob-top-bar">
        <div className="ob-logo">
          <Logo size={30} />
          <span className="ob-logo-text">FreeBuild</span>
        </div>
        <div className="ob-top-bar-links">
          <button
            type="button"
            className="ob-top-bar-link"
            onClick={toggle}
            title={t.onboarding.topBar.theme}
            aria-label={t.onboarding.topBar.theme}
          >
            <ThemeIcon size={18} />
          </button>
          <div className="ob-lang">
            <button
              type="button"
              className="ob-top-bar-link ob-lang-trigger"
              onClick={() => setLangOpen((v) => !v)}
              title={t.onboarding.topBar.language}
              aria-haspopup="true"
              aria-expanded={langOpen}
            >
              {LANG_CODE[locale]}
            </button>
            {langOpen && (
              <>
                <div className="ob-lang-backdrop" onClick={() => setLangOpen(false)} aria-hidden />
                <div className="ob-lang-menu" role="menu">
                  {locales.map((l) => (
                    <button
                      key={l}
                      type="button"
                      role="menuitemradio"
                      aria-checked={l === locale}
                      lang={l}
                      dir={isRtl(l) ? "rtl" : "ltr"}
                      className={`ob-lang-item${l === locale ? " is-active" : ""}`}
                      onClick={() => { setLocale(l); setLangOpen(false); }}
                    >
                      {LANG_NATIVE[l]}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          {/* Modified by Netie AI, 2026: the upstream marketing-site and GitHub links
              are gone. The upstream credit is shown only on Settings > Instance. The
              source link returns when BRAND_LINKS.github has a FreeBuild URL. */}
          {BRAND_LINKS.github && (
            <a className="ob-top-bar-link" href={BRAND_LINKS.github} target="_blank" rel="noopener noreferrer" title={t.onboarding.topBar.github}>
              <UiIcon name="github" size={18} />
            </a>
          )}
        </div>
      </div>

      {/* Main container */}
      <div className="ob-root">
        {step === "choose" && (
          <ChooseStep state={state} onUpdate={updateState} onNext={goNext} />
        )}
        {step === "selfhost-choice" && (
          <SelfhostChoiceStep state={state} onUpdate={updateState} onNext={goNext} onBack={goBack} />
        )}
        {step === "ssh" && (
          <SshStep state={state} onUpdate={updateState} onNext={goNext} onBack={goBack} />
        )}
        {step === "tunnel" && (
          <TunnelStep state={state} onUpdate={updateState} onNext={goNext} onBack={goBack} />
        )}
        {step === "preferences" && (
          <PreferencesStep state={state} onUpdate={updateState} onNext={goNext} onBack={goBack} />
        )}
        {step === "loading" && (
          <LoadingStep state={state} onBack={goBack} />
        )}
      </div>
    </>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense>
      <OnboardingInner />
    </Suspense>
  );
}
