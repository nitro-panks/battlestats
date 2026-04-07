"use client";

import React, { useEffect, useRef, useState } from 'react';

interface StreamerSubmissionModalProps {
    open: boolean;
    onClose: () => void;
}

type SubmitState = 'idle' | 'submitting' | 'success' | 'error';

interface FieldErrors {
    ign?: string;
    realm?: string;
    twitch_handle?: string;
    twitch_url?: string;
    non_field_errors?: string;
}

const inputClass =
    'w-full rounded border border-[var(--border)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:border-[var(--accent-mid)] focus:outline-none';

const StreamerSubmissionModal: React.FC<StreamerSubmissionModalProps> = ({ open, onClose }) => {
    const [ign, setIgn] = useState('');
    const [realm, setRealm] = useState('');
    const [twitchHandle, setTwitchHandle] = useState('');
    const [twitchUrl, setTwitchUrl] = useState('');
    const [website, setWebsite] = useState(''); // honeypot
    const [state, setState] = useState<SubmitState>('idle');
    const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
    const [genericError, setGenericError] = useState('');
    const loadedAtRef = useRef<number>(0);
    const ignInputRef = useRef<HTMLInputElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        loadedAtRef.current = Date.now();
        setState('idle');
        setFieldErrors({});
        setGenericError('');
        setIgn('');
        setRealm('');
        setTwitchHandle('');
        setTwitchUrl('');
        setWebsite('');
        setTimeout(() => ignInputRef.current?.focus(), 30);

        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    useEffect(() => {
        if (state !== 'success') return;
        const t = setTimeout(onClose, 2000);
        return () => clearTimeout(t);
    }, [state, onClose]);

    if (!open) return null;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setState('submitting');
        setFieldErrors({});
        setGenericError('');
        try {
            const res = await fetch('/api/streamer-submissions/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ign,
                    realm,
                    twitch_handle: twitchHandle.replace(/^@/, ''),
                    twitch_url: twitchUrl,
                    website,
                    form_loaded_at: loadedAtRef.current,
                }),
            });
            if (res.status === 201) {
                setState('success');
                return;
            }
            if (res.status === 400) {
                const body = await res.json().catch(() => ({}));
                const errs: FieldErrors = {};
                for (const k of ['ign', 'realm', 'twitch_handle', 'twitch_url'] as const) {
                    if (body[k]) errs[k] = Array.isArray(body[k]) ? body[k][0] : String(body[k]);
                }
                if (body.non_field_errors) {
                    errs.non_field_errors = Array.isArray(body.non_field_errors)
                        ? body.non_field_errors[0]
                        : String(body.non_field_errors);
                }
                setFieldErrors(errs);
                setGenericError(errs.non_field_errors || 'Please correct the errors below.');
                setState('error');
                return;
            }
            setGenericError('Something went wrong. Please try again later.');
            setState('error');
        } catch {
            setGenericError('Network error. Please try again.');
            setState('error');
        }
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
            onClick={(e) => {
                if (e.target === e.currentTarget) onClose();
            }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="streamer-submission-title"
        >
            <div
                ref={panelRef}
                className="w-full max-w-[480px] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5 shadow-xl"
            >
                <div className="mb-3 flex items-start justify-between">
                    <h2
                        id="streamer-submission-title"
                        className="text-base font-semibold text-[var(--text-primary)]"
                    >
                        Suggest a streamer
                    </h2>
                    <button
                        type="button"
                        onClick={onClose}
                        aria-label="Close"
                        className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    >
                        ×
                    </button>
                </div>

                {state === 'success' ? (
                    <div className="py-6 text-center text-sm text-[var(--text-primary)]">
                        Thanks! Your submission is queued for review.
                    </div>
                ) : (
                    <form onSubmit={handleSubmit} className="space-y-3">
                        <p className="text-xs text-[var(--text-secondary)]">
                            Know a WoWS player who streams on Twitch? Tell us and we&apos;ll review the submission.
                        </p>

                        {/* Honeypot — hidden from users, visible to naive bots */}
                        <input
                            type="text"
                            name="website"
                            tabIndex={-1}
                            aria-hidden="true"
                            autoComplete="off"
                            value={website}
                            onChange={(e) => setWebsite(e.target.value)}
                            style={{ position: 'absolute', left: '-9999px', width: 1, height: 1 }}
                        />

                        <div>
                            <label className="mb-1 block text-xs text-[var(--text-secondary)]">
                                In-game name
                            </label>
                            <input
                                ref={ignInputRef}
                                type="text"
                                value={ign}
                                onChange={(e) => setIgn(e.target.value)}
                                placeholder="bfk_ferlyfe"
                                required
                                minLength={3}
                                maxLength={32}
                                pattern="[A-Za-z0-9_\-]{3,32}"
                                className={inputClass}
                            />
                            {fieldErrors.ign && (
                                <p className="mt-1 text-xs text-red-500">{fieldErrors.ign}</p>
                            )}
                        </div>

                        <div>
                            <label className="mb-1 block text-xs text-[var(--text-secondary)]">
                                Realm (optional)
                            </label>
                            <select
                                value={realm}
                                onChange={(e) => setRealm(e.target.value)}
                                className={inputClass}
                            >
                                <option value="">—</option>
                                <option value="na">NA</option>
                                <option value="eu">EU</option>
                                <option value="asia">Asia</option>
                            </select>
                            {fieldErrors.realm && (
                                <p className="mt-1 text-xs text-red-500">{fieldErrors.realm}</p>
                            )}
                        </div>

                        <div>
                            <label className="mb-1 block text-xs text-[var(--text-secondary)]">
                                Twitch handle
                            </label>
                            <input
                                type="text"
                                value={twitchHandle}
                                onChange={(e) => setTwitchHandle(e.target.value)}
                                placeholder="bfk_fer1yfe"
                                required
                                minLength={3}
                                maxLength={25}
                                className={inputClass}
                            />
                            {fieldErrors.twitch_handle && (
                                <p className="mt-1 text-xs text-red-500">{fieldErrors.twitch_handle}</p>
                            )}
                        </div>

                        <div>
                            <label className="mb-1 block text-xs text-[var(--text-secondary)]">
                                Twitch channel URL
                            </label>
                            <input
                                type="url"
                                value={twitchUrl}
                                onChange={(e) => setTwitchUrl(e.target.value)}
                                placeholder="https://www.twitch.tv/bfk_fer1yfe"
                                required
                                className={inputClass}
                            />
                            {fieldErrors.twitch_url && (
                                <p className="mt-1 text-xs text-red-500">{fieldErrors.twitch_url}</p>
                            )}
                        </div>

                        {state === 'error' && genericError && (
                            <p className="text-xs text-red-500">{genericError}</p>
                        )}

                        <div className="flex justify-end gap-2 pt-2">
                            <button
                                type="button"
                                onClick={onClose}
                                className="rounded border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-page)]"
                            >
                                Cancel
                            </button>
                            <button
                                type="submit"
                                disabled={state === 'submitting'}
                                className="rounded bg-[var(--accent-mid)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--accent-dark)] disabled:opacity-50"
                            >
                                {state === 'submitting' ? 'Submitting…' : 'Submit'}
                            </button>
                        </div>
                    </form>
                )}
            </div>
        </div>
    );
};

export default StreamerSubmissionModal;
