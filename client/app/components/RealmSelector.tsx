"use client";

import React, { useEffect, useRef, useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faChevronDown, faGlobe } from '@fortawesome/free-solid-svg-icons';
import { useRealm, type Realm } from '../context/RealmContext';
import { usePathname, useRouter } from 'next/navigation';

interface RealmOption {
    value: Realm;
    label: string;
}

const REALM_OPTIONS: RealmOption[] = [
    { value: 'na', label: 'NA' },
    { value: 'eu', label: 'EU' },
];

const RealmSelector: React.FC = () => {
    const { realm, setRealm } = useRealm();
    const [open, setOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const router = useRouter();
    const pathname = usePathname();

    useEffect(() => {
        if (!open) {
            return;
        }

        const handleMouseDown = (event: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setOpen(false);
            }
        };

        document.addEventListener('mousedown', handleMouseDown);
        return () => document.removeEventListener('mousedown', handleMouseDown);
    }, [open]);

    useEffect(() => {
        if (!open) {
            return;
        }

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setOpen(false);
            }
        };

        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [open]);

    const handleRealmChange = (newRealm: Realm) => {
        setRealm(newRealm);
        setOpen(false);

        // If on a player or clan detail page, redirect to landing
        // since the entity may not exist on the other realm
        if (pathname.startsWith('/player/') || pathname.startsWith('/clan/')) {
            router.push('/');
        }
    };

    const currentLabel = realm.toUpperCase();

    return (
        <div ref={containerRef} className="relative">
            <button
                type="button"
                onClick={() => setOpen((prev) => !prev)}
                className="inline-flex items-center gap-1.5 rounded-md px-[10px] transition-colors"
                style={{
                    height: '28px',
                    border: '1px solid var(--border)',
                    backgroundColor: open ? 'var(--bg-hover)' : 'var(--bg-surface)',
                    color: 'var(--text-secondary)',
                    cursor: 'pointer',
                }}
                aria-label={`Realm: ${currentLabel}`}
                aria-expanded={open}
                aria-haspopup="listbox"
            >
                <FontAwesomeIcon icon={faGlobe} style={{ fontSize: '13px', opacity: 0.7 }} aria-hidden="true" />
                <span style={{ fontSize: '13px', fontWeight: 600 }}>{currentLabel}</span>
                <FontAwesomeIcon icon={faChevronDown} style={{ fontSize: '10px', marginLeft: '4px', opacity: 0.35 }} aria-hidden="true" />
            </button>

            {open && (
                <div
                    role="listbox"
                    aria-label="Select realm"
                    className="absolute right-0 z-50 mt-1 rounded-lg shadow-lg"
                    style={{
                        width: '100px',
                        top: 'calc(100% + 4px)',
                        border: '1px solid var(--border)',
                        backgroundColor: 'var(--bg-surface)',
                    }}
                >
                    {REALM_OPTIONS.map((option) => {
                        const isActive = realm === option.value;

                        return (
                            <button
                                key={option.value}
                                role="option"
                                aria-selected={isActive}
                                type="button"
                                onClick={() => handleRealmChange(option.value)}
                                className="flex w-full items-center justify-between rounded-md px-2 transition-colors"
                                style={{
                                    height: '32px',
                                    paddingLeft: '8px',
                                    paddingRight: '8px',
                                    color: isActive ? 'var(--text-primary)' : 'rgba(107, 114, 128, 0.6)',
                                    cursor: 'pointer',
                                    backgroundColor: 'transparent',
                                    border: 'none',
                                    fontWeight: isActive ? 600 : 400,
                                }}
                                onMouseEnter={(e) => {
                                    (e.currentTarget as HTMLButtonElement).style.backgroundColor = 'var(--bg-hover)';
                                }}
                                onMouseLeave={(e) => {
                                    (e.currentTarget as HTMLButtonElement).style.backgroundColor = 'transparent';
                                }}
                            >
                                <span style={{ fontSize: '13px' }}>
                                    {option.label}
                                </span>
                                {isActive && (
                                    <FontAwesomeIcon icon={faCheck} style={{ fontSize: '11px' }} aria-hidden="true" />
                                )}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default RealmSelector;
