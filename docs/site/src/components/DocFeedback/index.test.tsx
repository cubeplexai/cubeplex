import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import DocFeedback from './index';

declare global { interface Window { __cubepi_posthog?: { capture: (e: string, p: object) => void } } }

beforeEach(() => {
  window.__cubepi_posthog = { capture: vi.fn() };
});

describe('DocFeedback', () => {
  it('renders the prompt and two buttons', () => {
    render(<DocFeedback slug="/foo" version="0.3" locale="en" />);
    expect(screen.getByText(/was this page helpful/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /yes/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /no/i })).toBeInTheDocument();
  });

  it('captures a doc_feedback event with helpful=true on 👍', () => {
    const capture = vi.fn();
    window.__cubepi_posthog = { capture };
    render(<DocFeedback slug="/foo" version="0.3" locale="en" />);
    fireEvent.click(screen.getByRole('button', { name: /yes/i }));
    expect(capture).toHaveBeenCalledWith('doc_feedback', {
      slug: '/foo', helpful: true, version: '0.3', locale: 'en',
    });
    expect(screen.getByText(/thanks/i)).toBeInTheDocument();
  });

  it('shows a comment textarea on 👎 and captures doc_feedback_comment on submit', () => {
    const capture = vi.fn();
    window.__cubepi_posthog = { capture };
    render(<DocFeedback slug="/foo" version="0.3" locale="en" />);
    fireEvent.click(screen.getByRole('button', { name: /no/i }));
    expect(capture).toHaveBeenCalledWith('doc_feedback', {
      slug: '/foo', helpful: false, version: '0.3', locale: 'en',
    });
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'unclear example' } });
    fireEvent.click(screen.getByRole('button', { name: /submit/i }));
    expect(capture).toHaveBeenCalledWith('doc_feedback_comment', {
      slug: '/foo', version: '0.3', locale: 'en', comment: 'unclear example',
    });
  });
});
