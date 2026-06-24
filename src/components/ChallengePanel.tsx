import React, {useCallback, useEffect, useState} from 'react';



/* ── Types ────────────────────────────────────────────────────────── */

interface MCQOption {
  id: string;
  text: string;
}

interface MCQQuestion {
  id: number;
  sub_concept: string;
  dimension: string;
  question: string;
  options: MCQOption[];
  correct_option?: string; // TESTING AID — only present when fetched with ?reveal=true
}

interface MCQResult {
  is_correct: boolean;
  correct_option: string;
  explanations: Record<string, string>;
  dimension: string;
  sub_concept: string;
}

interface DimensionScore {
  total: number;
  correct: number;
}

interface Evaluation {
  accuracy: number;
  depth: number;
  reasoning: number;
  level: string;
  feedback: string;
}

interface Round {
  question: string;
  mode: string;
  answer?: string;
  evaluation?: Evaluation;
}

type ChallengeMode = 'idle' | 'mcq' | 'socratic';
type MCQState = 'loading' | 'answering' | 'feedback' | 'complete';
type SocraticState = 'loading' | 'asking' | 'evaluating' | 'complete';

const DIMENSION_LABELS: Record<string, string> = {
  recall: 'Recall',
  mechanism: 'Mechanism',
  tradeoff: 'Tradeoff',
  application: 'Application',
};

const LEVEL_LABELS: Record<string, string> = {
  surface: 'Surface',
  structural: 'Structural',
  deep: 'Deep',
  creative: 'Creative',
};

const LEVEL_COLORS: Record<string, string> = {
  surface: '#ef4444',
  structural: '#f59e0b',
  deep: '#10b981',
  creative: '#6366f1',
};

const MODE_LABELS: Record<string, string> = {
  explain: 'Explain',
  apply: 'Apply',
  contrast: 'Contrast',
  teach_back: 'Teach-back',
  debug: 'Debug',
};

/* ── Props ────────────────────────────────────────────────────────── */

interface Props {
  topicId: string;
  topicTitle: string;
  onUnderstandingChange?: (topicId: string, level: string) => void;
}

/* ── Component ────────────────────────────────────────────────────── */

export default function ChallengePanel({topicId, topicTitle, onUnderstandingChange}: Props) {
  const [mode, setMode] = useState<ChallengeMode>('idle');
  const [error, setError] = useState<string | null>(null);

  // MCQ state
  const [mcqState, setMcqState] = useState<MCQState>('loading');
  const [questions, setQuestions] = useState<MCQQuestion[]>([]);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [showHint, setShowHint] = useState(false); // TESTING AID — reveals MCQ answer
  const [mcqResult, setMcqResult] = useState<MCQResult | null>(null);
  const [mcqScores, setMcqScores] = useState<Record<string, DimensionScore>>({});
  const [mcqTotal, setMcqTotal] = useState({correct: 0, total: 0});
  const [answeredQuestions, setAnsweredQuestions] = useState<
    {question: MCQQuestion; result: MCQResult; selected: string}[]
  >([]);

  // Socratic state
  const [socraticState, setSocraticState] = useState<SocraticState>('loading');
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [rounds, setRounds] = useState<Round[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState('');
  const [currentMode, setCurrentMode] = useState('');
  const [answer, setAnswer] = useState('');
  const [roundNum, setRoundNum] = useState(0);
  const [maxRounds] = useState(5);
  const [finalLevel, setFinalLevel] = useState<string | null>(null);
  const [finalScores, setFinalScores] = useState<{accuracy: number; depth: number; reasoning: number} | null>(null);

  // Check for existing MCQ questions on mount
  const [hasExistingMCQ, setHasExistingMCQ] = useState(false);
  useEffect(() => {
    fetch(`/api/challenge/${topicId}/mcq/questions`)
      .then(r => r.json())
      .then(d => setHasExistingMCQ(d.count > 0))
      .catch(() => {});
  }, [topicId]);

  /* ── MCQ Flow ──────────────────────────────────────────────────── */

  const startMCQ = useCallback(async (regenerate = false) => {
    setMode('mcq');
    setMcqState('loading');
    setError(null);
    setCurrentIdx(0);
    setSelectedOption(null);
    setMcqResult(null);
    setMcqScores({});
    setMcqTotal({correct: 0, total: 0});
    setAnsweredQuestions([]);

    try {
      if (regenerate || !hasExistingMCQ) {
        const genRes = await fetch(`/api/challenge/${topicId}/mcq/generate`, {method: 'POST'});
        if (!genRes.ok) throw new Error(await genRes.text());
      }

      // TESTING AID: ?reveal=true returns correct_option so the dev hint can
      // show the answer. Drop the query param to disable.
      const res = await fetch(`/api/challenge/${topicId}/mcq/questions?reveal=true`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      if (!data.questions?.length) throw new Error('No questions generated');
      setQuestions(data.questions);
      setMcqState('answering');
    } catch (err) {
      setError((err as Error).message);
      setMode('idle');
    }
  }, [topicId, hasExistingMCQ]);

  const submitMCQAnswer = useCallback(async () => {
    if (!selectedOption || currentIdx >= questions.length) return;

    const q = questions[currentIdx];
    try {
      const res = await fetch(`/api/challenge/${topicId}/mcq/answer`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question_id: q.id, selected: selectedOption}),
      });
      if (!res.ok) throw new Error(await res.text());
      const result: MCQResult = await res.json();
      setMcqResult(result);
      setAnsweredQuestions(prev => [...prev, {question: q, result, selected: selectedOption}]);

      setMcqScores(prev => {
        const dim = result.dimension;
        const existing = prev[dim] || {total: 0, correct: 0};
        return {
          ...prev,
          [dim]: {total: existing.total + 1, correct: existing.correct + (result.is_correct ? 1 : 0)},
        };
      });
      setMcqTotal(prev => ({
        correct: prev.correct + (result.is_correct ? 1 : 0),
        total: prev.total + 1,
      }));
      setMcqState('feedback');
    } catch (err) {
      setError((err as Error).message);
    }
  }, [selectedOption, currentIdx, questions, topicId]);

  const nextQuestion = useCallback(() => {
    const nextIdx = currentIdx + 1;
    if (nextIdx >= questions.length) {
      setMcqState('complete');
    } else {
      setCurrentIdx(nextIdx);
      setSelectedOption(null);
      setMcqResult(null);
      setMcqState('answering');
    }
  }, [currentIdx, questions.length]);

  /* ── Socratic Flow ─────────────────────────────────────────────── */

  const startSocratic = useCallback(async () => {
    setMode('socratic');
    setSocraticState('loading');
    setError(null);
    setRounds([]);
    setFinalLevel(null);
    setFinalScores(null);

    try {
      const res = await fetch(`/api/challenge/${topicId}/start`, {method: 'POST'});
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setSessionId(data.session_id);
      setCurrentQuestion(data.question);
      setCurrentMode(data.mode);
      setRoundNum(data.round);
      setSocraticState('asking');
    } catch (err) {
      setError((err as Error).message);
      setMode('idle');
    }
  }, [topicId]);

  const submitSocraticAnswer = useCallback(async () => {
    if (!answer.trim() || !sessionId) return;
    setSocraticState('evaluating');
    setError(null);

    const submittedRound: Round = {question: currentQuestion, mode: currentMode, answer: answer.trim()};

    try {
      const res = await fetch(`/api/challenge/${sessionId}/answer`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({answer: answer.trim()}),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();

      submittedRound.evaluation = data.evaluation;
      setRounds(prev => [...prev, submittedRound]);
      setAnswer('');
      setRoundNum(data.round);

      if (data.completed) {
        setFinalLevel(data.final_level);
        setFinalScores(data.final_scores);
        setSocraticState('complete');
        onUnderstandingChange?.(topicId, data.final_level);
      } else if (data.follow_up_question) {
        setCurrentQuestion(data.follow_up_question);
        setCurrentMode(data.follow_up_mode || 'explain');
        setSocraticState('asking');
      } else {
        setSocraticState('complete');
      }
    } catch (err) {
      setError((err as Error).message);
      setSocraticState('asking');
    }
  }, [answer, sessionId, currentQuestion, currentMode, topicId, onUnderstandingChange]);

  /* ── Render: Idle ──────────────────────────────────────────────── */

  if (mode === 'idle') {
    return (
      <section className="gk-card gk-challenge">
        <div className="gk-card__label">Test your understanding</div>
        <p className="gk-challenge__desc">
          Start with MCQ to identify knowledge gaps, then prove deep understanding
          through Socratic dialogue with the Examiner.
        </p>
        <div className="gk-challenge__mode-select">
          <button type="button" className="gk-challenge__start" onClick={() => startMCQ()}>
            MCQ Challenge: {topicTitle}
          </button>
          <button
            type="button"
            className="gk-challenge__start gk-challenge__start--secondary"
            onClick={startSocratic}
          >
            Socratic Deep Dive
          </button>
        </div>
        {error && <p className="gk-challenge__error">{error}</p>}
      </section>
    );
  }

  /* ── Render: MCQ Loading ───────────────────────────────────────── */

  if (mode === 'mcq' && mcqState === 'loading') {
    return (
      <section className="gk-card gk-challenge">
        <div className="gk-card__label">Generating questions...</div>
        <p className="gk-challenge__desc">The Examiner is creating diagnostic questions from this topic's content.</p>
        <div className="gk-pulse" />
      </section>
    );
  }

  /* ── Render: MCQ Active ────────────────────────────────────────── */

  if (mode === 'mcq' && (mcqState === 'answering' || mcqState === 'feedback')) {
    const q = questions[currentIdx];
    const pct = mcqTotal.total > 0 ? Math.round((mcqTotal.correct / mcqTotal.total) * 100) : 0;

    return (
      <section className="gk-card gk-challenge">
        <div className="gk-card__label">
          MCQ — Question {currentIdx + 1} of {questions.length}
          <span className="gk-challenge__mcq-score">{mcqTotal.correct}/{mcqTotal.total} correct ({pct}%)</span>
        </div>

        {/* Dimension progress */}
        <div className="gk-challenge__dim-progress">
          {Object.entries(DIMENSION_LABELS).map(([key, label]) => {
            const s = mcqScores[key];
            const filled = s ? s.correct : 0;
            const attempted = s ? s.total : 0;
            return (
              <div key={key} className="gk-challenge__dim-bar">
                <span className="gk-challenge__dim-label">{label}</span>
                <div className="gk-challenge__dim-track">
                  <div
                    className="gk-challenge__dim-fill"
                    style={{
                      width: attempted > 0 ? `${(filled / Math.max(1, attempted)) * 100}%` : '0%',
                      background: filled === attempted && attempted > 0 ? '#10b981' : '#f59e0b',
                    }}
                  />
                </div>
                <span className="gk-challenge__dim-count">{filled}/{attempted}</span>
              </div>
            );
          })}
        </div>

        {/* Question */}
        <div className="gk-challenge__mcq-question">
          <span className="gk-challenge__mode-badge">{DIMENSION_LABELS[q.dimension] || q.dimension}</span>
          <p>{q.question}</p>
        </div>

        {/* Options */}
        <div className="gk-challenge__mcq-options">
          {q.options.map(opt => {
            let optClass = 'gk-challenge__mcq-opt';
            if (mcqState === 'feedback' && mcqResult) {
              if (opt.id === mcqResult.correct_option) optClass += ' gk-challenge__mcq-opt--correct';
              else if (opt.id === selectedOption && !mcqResult.is_correct) optClass += ' gk-challenge__mcq-opt--wrong';
            } else if (opt.id === selectedOption) {
              optClass += ' gk-challenge__mcq-opt--selected';
            }
            // TESTING AID: highlight the correct answer while still answering.
            const isHinted = showHint && mcqState === 'answering' && opt.id === q.correct_option;
            if (isHinted) optClass += ' gk-challenge__mcq-opt--correct';
            return (
              <button
                key={opt.id}
                type="button"
                className={optClass}
                onClick={() => mcqState === 'answering' && setSelectedOption(opt.id)}
                disabled={mcqState === 'feedback'}
              >
                <span className="gk-challenge__mcq-opt-id">{opt.id.toUpperCase()}</span>
                <span className="gk-challenge__mcq-opt-text">{opt.text}</span>
                {isHinted && <span className="gk-challenge__mcq-hint-badge">✓ answer</span>}
              </button>
            );
          })}
        </div>

        {/* TESTING AID — answer hint toggle (remove this block + showHint state to disable) */}
        {mcqState === 'answering' && q.correct_option && (
          <button
            type="button"
            className="gk-challenge__hint-toggle"
            onClick={() => setShowHint(h => !h)}
            title="Dev aid: reveal the correct answer to move through assessments"
          >
            {showHint
              ? `Hint: answer is ${q.correct_option.toUpperCase()} (hide)`
              : '💡 Show answer hint (dev)'}
          </button>
        )}

        {/* Feedback */}
        {mcqState === 'feedback' && mcqResult && (
          <div className={`gk-challenge__mcq-feedback ${mcqResult.is_correct ? 'gk-challenge__mcq-feedback--correct' : 'gk-challenge__mcq-feedback--wrong'}`}>
            <div className="gk-challenge__mcq-feedback-header">
              {mcqResult.is_correct ? 'Correct' : 'Incorrect'}
            </div>
            {q.options.map(opt => (
              <div key={opt.id} className="gk-challenge__mcq-explanation">
                <strong>{opt.id.toUpperCase()}:</strong> {mcqResult.explanations[opt.id]}
              </div>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="gk-challenge__actions">
          {mcqState === 'answering' ? (
            <button
              type="button"
              className="gk-challenge__submit"
              onClick={submitMCQAnswer}
              disabled={!selectedOption}
            >
              Submit Answer
            </button>
          ) : (
            <button type="button" className="gk-challenge__submit" onClick={nextQuestion}>
              {currentIdx + 1 < questions.length ? 'Next Question' : 'See Results'}
            </button>
          )}
          <button
            type="button"
            className="gk-challenge__cancel"
            onClick={() => setMode('idle')}
          >
            Cancel
          </button>
        </div>

        {error && <p className="gk-challenge__error">{error}</p>}
      </section>
    );
  }

  /* ── Render: MCQ Complete ──────────────────────────────────────── */

  if (mode === 'mcq' && mcqState === 'complete') {
    const pct = mcqTotal.total > 0 ? Math.round((mcqTotal.correct / mcqTotal.total) * 100) : 0;
    const readyForSocratic = pct >= 60;
    const scoreColor = pct >= 80 ? '#10b981' : pct >= 60 ? '#f59e0b' : '#ef4444';

    return (
      <section className="gk-card gk-challenge">
        <div className="gk-card__label">MCQ Results</div>

        <div className="gk-challenge__mcq-result-header">
          <span className="gk-challenge__mcq-big-score" style={{color: scoreColor}}>{pct}%</span>
          <span className="gk-challenge__mcq-big-label">{mcqTotal.correct} of {mcqTotal.total} correct</span>
        </div>

        <div className="gk-challenge__dim-progress" style={{marginBottom: '1rem'}}>
          {Object.entries(DIMENSION_LABELS).map(([key, label]) => {
            const s = mcqScores[key];
            if (!s) return null;
            const dimPct = Math.round((s.correct / s.total) * 100);
            return (
              <div key={key} className="gk-challenge__dim-bar">
                <span className="gk-challenge__dim-label">{label}</span>
                <div className="gk-challenge__dim-track">
                  <div
                    className="gk-challenge__dim-fill"
                    style={{
                      width: `${dimPct}%`,
                      background: dimPct >= 80 ? '#10b981' : dimPct >= 50 ? '#f59e0b' : '#ef4444',
                    }}
                  />
                </div>
                <span className="gk-challenge__dim-count">{s.correct}/{s.total} ({dimPct}%)</span>
              </div>
            );
          })}
        </div>

        {/* Review wrong answers */}
        {answeredQuestions.filter(a => !a.result.is_correct).length > 0 && (
          <div className="gk-challenge__mcq-review">
            <div className="gk-challenge__mcq-review-title">Areas to revisit</div>
            {answeredQuestions.filter(a => !a.result.is_correct).map((a, i) => (
              <div key={i} className="gk-challenge__mcq-review-item">
                <span className="gk-challenge__mode-badge">{DIMENSION_LABELS[a.result.dimension]}</span>
                <span className="gk-challenge__mcq-review-concept">{a.result.sub_concept.replace(/-/g, ' ')}</span>
                <p className="gk-challenge__mcq-review-correction">
                  {a.result.explanations[a.result.correct_option]}
                </p>
              </div>
            ))}
          </div>
        )}

        <div className="gk-challenge__actions">
          {readyForSocratic ? (
            <button type="button" className="gk-challenge__start" onClick={startSocratic}>
              Proceed to Socratic Deep Dive
            </button>
          ) : (
            <p className="gk-challenge__mcq-not-ready">
              Score 60%+ to unlock the Socratic assessment. Review the topic content and try again.
            </p>
          )}
          <button type="button" className="gk-challenge__retry" onClick={() => startMCQ(true)}>
            Retake MCQ
          </button>
          <button type="button" className="gk-challenge__cancel" onClick={() => setMode('idle')}>
            Back
          </button>
        </div>
      </section>
    );
  }

  /* ── Render: Socratic ──────────────────────────────────────────── */

  if (mode === 'socratic' && socraticState === 'loading') {
    return (
      <section className="gk-card gk-challenge">
        <div className="gk-card__label">Preparing Socratic assessment...</div>
        <div className="gk-pulse" />
      </section>
    );
  }

  if (mode === 'socratic') {
    return (
      <section className="gk-card gk-challenge">
        <div className="gk-card__label">
          Socratic — Round {roundNum} of {maxRounds}
        </div>

        {rounds.map((r, i) => (
          <div key={i} className="gk-challenge__round">
            <div className="gk-challenge__q">
              <span className="gk-challenge__mode-badge">{MODE_LABELS[r.mode] || r.mode}</span>
              {r.question}
            </div>
            {r.answer && (
              <div className="gk-challenge__a">
                <span className="gk-challenge__a-label">You:</span> {r.answer}
              </div>
            )}
            {r.evaluation && (
              <div className="gk-challenge__eval">
                <div className="gk-challenge__scores">
                  <ScorePill label="Accuracy" value={r.evaluation.accuracy} max={3} />
                  <ScorePill label="Depth" value={r.evaluation.depth} max={3} />
                  <ScorePill label="Reasoning" value={r.evaluation.reasoning} max={2} />
                  <span className="gk-challenge__level-badge" style={{background: LEVEL_COLORS[r.evaluation.level] || '#94a3b8'}}>
                    {LEVEL_LABELS[r.evaluation.level] || r.evaluation.level}
                  </span>
                </div>
                <div className="gk-challenge__feedback">{r.evaluation.feedback}</div>
              </div>
            )}
          </div>
        ))}

        {(socraticState === 'asking' || socraticState === 'evaluating') && (
          <div className="gk-challenge__current">
            <div className="gk-challenge__q">
              <span className="gk-challenge__mode-badge">{MODE_LABELS[currentMode] || currentMode}</span>
              {currentQuestion}
            </div>
            <textarea
              className="gk-textarea gk-challenge__input"
              placeholder="Type your answer..."
              value={answer}
              onChange={e => setAnswer(e.target.value)}
              disabled={socraticState === 'evaluating'}
              onKeyDown={e => { if (e.key === 'Enter' && e.metaKey && answer.trim()) submitSocraticAnswer(); }}
            />
            <div className="gk-challenge__actions">
              <button
                type="button"
                className="gk-challenge__submit"
                onClick={submitSocraticAnswer}
                disabled={!answer.trim() || socraticState === 'evaluating'}
              >
                {socraticState === 'evaluating' ? 'Evaluating...' : 'Submit Answer'}
              </button>
              <span className="gk-challenge__hint">Cmd+Enter to submit</span>
            </div>
            {socraticState === 'evaluating' && <div className="gk-pulse" />}
          </div>
        )}

        {socraticState === 'complete' && finalLevel && (
          <div className="gk-challenge__result">
            <div className="gk-challenge__result-header">
              <span className="gk-challenge__final-level" style={{color: LEVEL_COLORS[finalLevel] || '#94a3b8'}}>
                {LEVEL_LABELS[finalLevel] || finalLevel}
              </span>
              <span className="gk-challenge__result-label">Understanding Level</span>
            </div>
            {finalScores && (
              <div className="gk-challenge__final-scores">
                <ScoreBar label="Accuracy" value={finalScores.accuracy} max={3} />
                <ScoreBar label="Depth" value={finalScores.depth} max={3} />
                <ScoreBar label="Reasoning" value={finalScores.reasoning} max={2} />
              </div>
            )}
            <button type="button" className="gk-challenge__retry" onClick={() => setMode('idle')}>
              Done
            </button>
          </div>
        )}

        {error && <p className="gk-challenge__error">{error}</p>}
      </section>
    );
  }

  return null;
}

/* ── Sub-components ─────────────────────────────────────────────── */

function ScorePill({label, value, max}: {label: string; value: number; max: number}) {
  const pct = value / max;
  const color = pct >= 0.8 ? '#10b981' : pct >= 0.5 ? '#f59e0b' : '#ef4444';
  return (
    <span className="gk-challenge__score-pill" style={{borderColor: color}}>
      <span style={{color, fontWeight: 600}}>{value}</span>
      <span style={{color: 'var(--gk-muted)', fontSize: '0.6rem'}}>/{max} {label}</span>
    </span>
  );
}

function ScoreBar({label, value, max}: {label: string; value: number; max: number}) {
  const pct = Math.round((value / max) * 100);
  const color = pct >= 80 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
  return (
    <div className="gk-challenge__score-bar">
      <span className="gk-challenge__score-bar-label">{label}</span>
      <div className="gk-challenge__score-bar-track">
        <div className="gk-challenge__score-bar-fill" style={{width: `${pct}%`, background: color}} />
      </div>
      <span className="gk-challenge__score-bar-value" style={{color}}>{value}/{max}</span>
    </div>
  );
}
