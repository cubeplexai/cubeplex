import React, { useState } from 'react';
import styles from './styles.module.css';

interface Props {
  slug: string;
  version: string;
  locale: string;
}

type Phase = 'ask' | 'thanks' | 'comment' | 'submitted';

function capture(event: string, payload: object) {
  const ph = (window as any).__cubepi_posthog;
  if (ph && typeof ph.capture === 'function') ph.capture(event, payload);
}

export default function DocFeedback({ slug, version, locale }: Props): React.ReactElement {
  const [phase, setPhase] = useState<Phase>('ask');
  const [comment, setComment] = useState('');

  const onYes = () => {
    capture('doc_feedback', { slug, helpful: true, version, locale });
    setPhase('thanks');
  };
  const onNo = () => {
    capture('doc_feedback', { slug, helpful: false, version, locale });
    setPhase('comment');
  };
  const onSubmit = () => {
    capture('doc_feedback_comment', { slug, version, locale, comment });
    setPhase('submitted');
  };

  return (
    <aside className={styles.box}>
      {phase === 'ask' && (
        <>
          <span className={styles.q}>Was this page helpful?</span>
          <button type="button" className={styles.btn} onClick={onYes} aria-label="Yes">👍</button>
          <button type="button" className={styles.btn} onClick={onNo}  aria-label="No">👎</button>
        </>
      )}
      {phase === 'thanks' && <span className={styles.q}>Thanks!</span>}
      {phase === 'comment' && (
        <div className={styles.commentWrap}>
          <span className={styles.q}>What was missing?</span>
          <textarea className={styles.textarea} value={comment} onChange={(e) => setComment(e.target.value)} rows={3} />
          <button type="button" className={styles.btn} onClick={onSubmit}>Submit</button>
        </div>
      )}
      {phase === 'submitted' && <span className={styles.q}>Thanks — we'll review it.</span>}
    </aside>
  );
}
