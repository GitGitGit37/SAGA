import { AlertTriangle, ArrowLeft, Bot, Mic, Send, ShieldAlert, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ErrorBox, Loading } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/form";
import { api, type AssetDetail, type AssetSummary, type RecallResult, type TimelineEvent } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { RENTER_LANGUAGES, renterText, type RenterCopyKey } from "@/lib/renterLanguage";
import { humanize } from "@/lib/utils";

const EXPERIENCE_KEYS = ["ask.firstTime", "ask.someExperience", "ask.experienced"] as const;
type Turn = { question: string; answer?: RecallResult; localReply?: string; error?: string };

type SpeechAlternative = { transcript: string };
type SpeechResult = ArrayLike<SpeechAlternative>;
type SpeechResultEvent = { results: ArrayLike<SpeechResult> };
type SpeechRecognizer = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

const QUESTION_STOP_WORDS: Record<"en" | "es" | "fr", Set<string>> = {
  en: new Set([
  "about", "and", "are", "can", "could", "does", "for", "from", "how", "is", "it",
  "machine", "mean", "me", "my", "of", "on", "please", "say", "tell", "that", "the",
  "this", "to", "what", "when", "where", "which", "who", "why", "with", "would",
  ]),
  es: new Set(["acerca", "algo", "como", "cual", "cuando", "de", "del", "donde", "el", "en", "es", "esta", "este", "la", "las", "lo", "los", "maquina", "mi", "para", "por", "que", "se", "sobre", "un", "una", "y"]),
  fr: new Set(["a", "au", "aux", "avec", "comment", "dans", "de", "des", "du", "elle", "en", "est", "la", "le", "les", "ma", "machine", "mon", "ou", "par", "pour", "que", "quel", "quelle", "quand", "qui", "sur", "un", "une", "et"]),
};

function normalizeText(value: string): string {
  return value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function questionTerms(question: string, language: "en" | "es" | "fr"): string[] {
  return [...new Set(
    normalizeText(question).match(/[a-z0-9]+/g)?.filter(
      (word) => word.length > 2 && !QUESTION_STOP_WORDS[language].has(word),
    ) ?? [],
  )];
}

function relevantMemories(result: RecallResult, question: string, language: "en" | "es" | "fr") {
  const terms = questionTerms(question, language);
  if (terms.length === 0) return [];
  const requiredMatches = Math.ceil(terms.length / 2);
  return result.memories.filter((memory) => {
    const content = new Set(normalizeText(memory.content).match(/[a-z0-9]+/g) ?? []);
    return terms.filter((term) => content.has(term)).length >= requiredMatches;
  });
}

function interpolate(language: "en" | "es" | "fr", key: RenterCopyKey, values: Record<string, string> = {}): string {
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, value),
    renterText(language, key),
  );
}

function renterAnswer(result: RecallResult, question: string, language: "en" | "es" | "fr"): string {
  if (result.generated_by !== "template") return result.answer;

  const matchingMemories = relevantMemories(result, question, language);
  const matchingNotes = matchingMemories.filter((memory) => memory.owner_type === "observation");
  if (matchingNotes.length > 0) {
    const note = matchingNotes[0].content.split(": ").slice(1).join(": ");
    return interpolate(language, "ask.savedNote", { note });
  }
  if (matchingMemories.some((memory) => memory.owner_type === "inference")) {
    return renterText(language, "ask.alertUnclear");
  }
  return interpolate(language, "ask.notFound", { question: question.trim() });
}

function localReply(question: string, language: "en" | "es" | "fr"): string | null {
  const normalized = normalizeText(question).replace(/[.!?,¿¡]+/g, " ").trim().replace(/\s+/g, " ");
  const greetings = {
    en: /^(hi|hello|hey|good morning|good afternoon|good evening|how are you)( there)?$/,
    es: /^(hola|buenos dias|buenas tardes|buenas noches|como estas|como esta)$/,
    fr: /^(bonjour|salut|bonsoir|comment allez vous|comment ca va)$/,
  };
  if (greetings[language].test(normalized)) {
    return renterText(language, "ask.greeting");
  }
  const thanks = { en: /^(thanks|thank you|cheers)$/, es: /^(gracias|muchas gracias)$/, fr: /^(merci|merci beaucoup)$/ };
  if (thanks[language].test(normalized)) {
    return renterText(language, "ask.thanks");
  }
  if (questionTerms(question, language).length < 2) {
    return interpolate(language, "ask.clarify", { question: question.trim() });
  }
  return null;
}

function speechInputConstructor(): (new () => SpeechRecognizer) | undefined {
  const speechWindow = window as typeof window & {
    SpeechRecognition?: new () => SpeechRecognizer;
    webkitSpeechRecognition?: new () => SpeechRecognizer;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
}

function MachineCard({ asset, language }: { asset: AssetSummary; language: "en" | "es" | "fr" }) {
  const hasAlert = asset.health !== "ok";
  const typeKey = `machine.type.${asset.asset_type}` as RenterCopyKey;
  const typeName = asset.asset_type in { dozer: true, excavator: true, wheel_loader: true }
    ? renterText(language, typeKey)
    : humanize(asset.asset_type);
  return (
    <Link to={`/assets/${asset.asset_tag}`} className="block">
      <Card className="flex h-full flex-col gap-3 p-5 transition-shadow hover:shadow-md">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">{asset.name}</h2>
            <p className="mt-1 text-sm text-zinc-500">
              Cat {asset.model} {typeName}
            </p>
          </div>
          {hasAlert ? (
            <Badge tone="warn"><AlertTriangle /> {renterText(language, "machine.reviewNotes")}</Badge>
          ) : (
            <Badge tone="ok">{renterText(language, "machine.noAlerts")}</Badge>
          )}
        </div>
        <p className="text-sm text-zinc-600">
          {hasAlert
            ? renterText(language, "machine.readBeforeStart")
            : renterText(language, "machine.openGuide")}
        </p>
        <div className="mt-auto flex items-center justify-between border-t border-zinc-100 pt-3 text-xs text-zinc-500">
          <span>{asset.site}</span>
          <span className="font-medium text-zinc-800">{renterText(language, "machine.open")}</span>
        </div>
      </Card>
    </Link>
  );
}

export function RenterHome() {
  const { data, error, loading } = useLoad(api.assets, []);
  const { renterLanguage: language } = useSession();
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading label={renterText(language, "home.loading")} />;

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">{renterText(language, "home.title")}</h1>
      <p className="mb-6 mt-1 text-sm text-zinc-600">{renterText(language, "home.subtitle")}</p>
      {data.length === 0 ? (
        <Card className="p-6 text-center text-sm text-zinc-600">{renterText(language, "home.empty")}</Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {data.map((asset) => <MachineCard key={asset.id} asset={asset} language={language} />)}
        </div>
      )}
    </div>
  );
}

function renterNoteLabel(event: TimelineEvent, language: "en" | "es" | "fr"): string {
  if (!("observation" in event)) return renterText(language, "before.machineNote");
  const role = event.observation.payload.author_role;
  if (role === "technician") return renterText(language, "before.checkedShop");
  if (role === "operator") return renterText(language, "before.operatorReport");
  if (role === "fleet_manager") return renterText(language, "before.rentalTeam");
  return renterText(language, "before.machineNote");
}

function BeforeYouStart({ machine, language }: { machine: AssetDetail; language: "en" | "es" | "fr" }) {
  const safetyItems = machine.live_inferences.filter((inference) => inference.is_safety_critical || inference.escalated);
  const notes = machine.events
    .filter((event) =>
      ["maintenance_note", "inspection_note"].includes(event.type) && "observation" in event,
    )
    .slice(0, 3);
  const alerts = machine.live_inferences.filter((inference) => inference.status !== "superseded").slice(0, 3);

  return (
    <section aria-labelledby="before-start-heading">
      <h2 id="before-start-heading" className="mb-3 text-xl font-bold">{renterText(language, "before.title")}</h2>
      <Card className="space-y-4 p-4 sm:p-5">
        {safetyItems.map((item) => (
          <div key={item.id} className="flex gap-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900">
            <ShieldAlert className="mt-0.5 size-5 shrink-0" />
            <div>
              <div className="font-semibold">{renterText(language, "before.safetyWarning")}</div>
              <p className="mt-1">{renterText(language, "before.stop")}</p>
              <details className="mt-2 text-xs">
                <summary className="w-fit cursor-pointer underline">{renterText(language, "before.details")} ({RENTER_LANGUAGES[0].label})</summary>
                <p className="mt-1 font-medium">{item.title}</p>
                {item.recommended_action && <p className="mt-1">{item.recommended_action}</p>}
              </details>
            </div>
          </div>
        ))}

        {alerts.map((item) => (
          <div key={item.id} className="flex gap-3 text-sm leading-relaxed">
            <span className="mt-0.5 text-amber-700" aria-hidden="true">•</span>
            <div>
              <Badge tone="warn">{renterText(language, "before.alert")}</Badge>
              <div className="mt-1 font-medium">{renterText(language, "before.alertSummary")}</div>
              <p className="mt-1 text-xs text-zinc-500">{renterText(language, "before.askAlert")}</p>
              <details className="mt-2 text-xs text-zinc-600">
                <summary className="w-fit cursor-pointer underline">
                  {renterText(language, "before.details")} ({RENTER_LANGUAGES[0].label})
                </summary>
                <p className="mt-1 font-medium">{item.title}</p>
                {item.recommended_action && <p className="mt-1">{item.recommended_action}</p>}
              </details>
            </div>
          </div>
        ))}

        {notes.map((event, index) => {
          if (!("observation" in event)) return null;
          const note = event.observation;
          return (
            <div key={`${note.id}-${index}`} className="flex gap-3 text-sm leading-relaxed">
              <span className="mt-0.5 text-zinc-400" aria-hidden="true">•</span>
              <div>
                <Badge tone="ok">
                  {renterNoteLabel(event, language)}
                  {language !== "en" && ` (${RENTER_LANGUAGES[0].label})`}
                </Badge>
                <p className="mt-1">{note.text || "A note was saved for this machine."}</p>
                <p className="mt-1 text-xs text-zinc-500">
                  {new Date(note.observed_at).toLocaleString(
                    RENTER_LANGUAGES.find((item) => item.value === language)?.speech,
                    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
                  )}
                </p>
              </div>
            </div>
          );
        })}

        {alerts.length === 0 && notes.length === 0 && safetyItems.length === 0 && (
          <p className="text-sm text-zinc-600">{renterText(language, "before.empty")}</p>
        )}
      </Card>

      <details className="mt-4 rounded-lg border border-zinc-200 bg-white">
        <summary className="cursor-pointer list-none px-4 py-4 font-semibold marker:hidden">
          <span className="mr-2 text-zinc-500">▸</span> {renterText(language, "before.safetyRules")}
        </summary>
        <div className="border-t border-zinc-100 px-4 py-4 text-sm leading-relaxed text-zinc-700">
          {safetyItems.length > 0 ? (
            <ul className="list-disc space-y-2 pl-5">
              {safetyItems.map((item) => (
                <li key={item.id}>
                  {item.recommended_action || item.title} If you’re unsure, stop and contact your rental team or site supervisor.
                </li>
              ))}
            </ul>
          ) : (
            <p>{renterText(language, "before.noSafetyRules")}</p>
          )}
        </div>
      </details>
    </section>
  );
}

function AskThisMachine({ machine }: { machine: AssetDetail }) {
  const { renterLanguage: language, setRenterLanguage } = useSession();
  const [experience, setExperience] = useState(0);
  const [readAloud, setReadAloud] = useState(false);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognition = useRef<SpeechRecognizer | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  useEffect(() => () => recognition.current?.stop(), []);

  const ask = async (rawQuestion: string) => {
    const trimmed = rawQuestion.trim();
    if (!trimmed || busy) return;
    setQuestion("");
    setError(null);
    const immediateReply = localReply(trimmed, language);
    if (immediateReply) {
      setTurns((current) => [...current, { question: trimmed, localReply: immediateReply }]);
      if (readAloud && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(immediateReply);
        utterance.lang = RENTER_LANGUAGES.find((item) => item.value === language)?.speech ?? "en-US";
        window.speechSynthesis.speak(utterance);
      }
      return;
    }
    setBusy(true);
    setTurns((current) => [...current, { question: trimmed }]);
    const selectedLanguage = RENTER_LANGUAGES.find((item) => item.value === language)!;
    const prompt = [
      `I am renting ${machine.name} and my equipment experience is: ${renterText(language, EXPERIENCE_KEYS[experience])}.`,
      `Answer in ${selectedLanguage.name} using clear, everyday language. Explain unfamiliar machine terms briefly.`,
      "Use only this machine's saved information. Do not guess. If the information is missing, say so and suggest asking the rental team.",
      `My question: ${trimmed}`,
    ].join("\n");

    try {
      const answer = await api.recall({ question: prompt, asset_tag: machine.asset_tag });
      setTurns((current) => current.map((turn, index) =>
        index === current.length - 1 ? { ...turn, answer } : turn,
      ));
      if (readAloud && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(renterAnswer(answer, trimmed, language));
        utterance.lang = selectedLanguage.speech;
        window.speechSynthesis.speak(utterance);
      }
    } catch (e) {
      const message = (e as Error).message;
      setTurns((current) => current.map((turn, index) =>
        index === current.length - 1 ? { ...turn, error: message } : turn,
      ));
    } finally {
      setBusy(false);
    }
  };

  const toggleVoiceInput = () => {
    if (recognition.current) {
      recognition.current.stop();
      recognition.current = null;
      setListening(false);
      return;
    }

    const SpeechRecognition = speechInputConstructor();
    if (!SpeechRecognition) {
      setError(renterText(language, "ask.voiceUnsupported"));
      return;
    }

    setError(null);
    const input = new SpeechRecognition();
    input.lang = RENTER_LANGUAGES.find((item) => item.value === language)?.speech ?? "en-US";
    input.interimResults = false;
    input.continuous = false;
    input.onresult = (event) => {
      setQuestion(event.results[0]?.[0]?.transcript ?? "");
      setListening(false);
      recognition.current = null;
    };
    input.onerror = () => {
      setError(renterText(language, "ask.voiceError"));
      setListening(false);
      recognition.current = null;
    };
    input.onend = () => {
      setListening(false);
      recognition.current = null;
    };
    try {
      recognition.current = input;
      setListening(true);
      input.start();
    } catch (e) {
      recognition.current = null;
      setListening(false);
      setError((e as Error).message);
    }
  };

  return (
    <section className="mt-5 rounded-lg border border-zinc-200 bg-white">
      <div className="border-b border-zinc-100 px-4 py-4">
        <h2 className="text-lg font-bold">{renterText(language, "ask.title")}</h2>
        <p className="mt-1 text-sm text-zinc-600">{renterText(language, "ask.subtitle")}</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-zinc-100 px-4 py-3 text-sm">
        <div className="flex items-center gap-2">
          <label htmlFor="renter-language" className="text-zinc-600">{renterText(language, "ask.language")}</label>
          <select
            id="renter-language"
            value={language}
            onChange={(event) => setRenterLanguage(event.target.value as "en" | "es" | "fr")}
            className="rounded border border-zinc-300 bg-white px-2 py-1.5"
          >
            {RENTER_LANGUAGES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </div>
        <label className="flex items-center gap-2">
          <span className="text-zinc-600">{renterText(language, "ask.experience")}</span>
          <select
            value={experience}
            onChange={(event) => setExperience(Number(event.target.value))}
            className="rounded border border-zinc-300 bg-white px-2 py-1.5"
          >
            {EXPERIENCE_KEYS.map((key, index) => (
              <option key={key} value={index}>{renterText(language, key)}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-zinc-700">
          <input
            type="checkbox"
            checked={readAloud}
            onChange={(event) => setReadAloud(event.target.checked)}
          />
          <Volume2 className="size-4" />
          {renterText(language, "ask.readAloud")}
        </label>
      </div>

      <div className="max-h-[50vh] min-h-36 space-y-4 overflow-y-auto p-4" aria-live="polite">
        {turns.length === 0 && (
          <div className="flex items-start gap-2 text-sm text-zinc-500">
            <Bot className="mt-0.5 size-4 shrink-0" />
            <p>{renterText(language, "ask.try")}</p>
          </div>
        )}
        {turns.map((turn, index) => (
          <div key={`${turn.question}-${index}`} className="space-y-3">
            <p className="ml-auto w-fit max-w-[85%] rounded-lg bg-zinc-900 px-3 py-2 text-sm text-white">
              {turn.question}
            </p>
            <div className="flex gap-2">
              <Bot className="mt-1 size-4 shrink-0 text-amber-700" />
              <div className="min-w-0 text-sm leading-relaxed text-zinc-800">
                {turn.error && <p className="text-red-700">{turn.error}</p>}
                {!turn.answer && !turn.localReply && !turn.error && <p className="text-zinc-500">{renterText(language, "ask.checking")}</p>}
                {turn.localReply && <p className="whitespace-pre-wrap">{turn.localReply}</p>}
                {turn.answer && (
                  <p className="whitespace-pre-wrap">{renterAnswer(turn.answer, turn.question, language)}</p>
                )}
              </div>
            </div>
          </div>
        ))}
        {busy && <p className="text-sm text-zinc-500">{renterText(language, "ask.finding")}</p>}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(event) => { event.preventDefault(); void ask(question); }}
        className="flex items-center gap-2 border-t border-zinc-200 p-3"
      >
        <button
          type="button"
          onClick={toggleVoiceInput}
          aria-label={renterText(language, listening ? "ask.stopVoice" : "ask.voice")}
          title={listening ? renterText(language, "ask.stopVoice") : renterText(language, "ask.voice")}
          className={`grid size-11 shrink-0 place-items-center rounded-md border ${listening ? "border-amber-500 bg-amber-100 text-amber-900" : "border-zinc-300 text-zinc-700 hover:bg-zinc-50"}`}
        >
          <Mic className="size-5" />
        </button>
        <Input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={renterText(language, "ask.placeholder")}
          aria-label={renterText(language, "ask.title")}
          className="h-11 min-w-0 flex-1"
        />
        <Button type="submit" disabled={busy || !question.trim()} variant="brand" className="h-11 px-5">
          <Send /> {renterText(language, "ask.button")}
        </Button>
      </form>
      {error && <p role="alert" className="px-4 pb-3 text-sm text-red-700">{error}</p>}
    </section>
  );
}

export function RenterMachinePage({ tag }: { tag: string }) {
  const { data: machine, error, loading } = useLoad(() => api.asset(tag), [tag]);
  const { renterLanguage: language } = useSession();
  if (error) return <ErrorBox error={error} />;
  if (loading || !machine) return <Loading label={renterText(language, "machine.openGuide")} />;

  const latest = machine.telematics.at(-1);
  const hours = Object.entries(latest ?? {}).find(([key, value]) =>
    /(?:engine|machine|operating)_hours/i.test(key) && typeof value === "number",
  );

  return (
    <div>
      <Link to="/" className="mb-3 inline-flex items-center gap-2 text-sm text-zinc-600 hover:text-zinc-950">
        <ArrowLeft className="size-4" /> {renterText(language, "machine.back")}
      </Link>
      <header className="border-b-4 border-amber-400 bg-zinc-950 px-4 py-5 text-white sm:px-6">
        <h1 className="text-2xl font-bold">
          Cat {machine.model}{" "}
          {machine.asset_type in { dozer: true, excavator: true, wheel_loader: true }
            ? renterText(language, `machine.type.${machine.asset_type}` as RenterCopyKey)
            : humanize(machine.asset_type)}
        </h1>
        <p className="mt-1 text-sm text-zinc-300">
          {machine.name} · {renterText(language, "machine.serial")} {machine.serial_number}
          {hours && <> · {Number(hours[1]).toLocaleString()} hours</>}
        </p>
      </header>
      <div className="py-5">
        <BeforeYouStart machine={machine} language={language} />
        <AskThisMachine machine={machine} />
      </div>
    </div>
  );
}
