"use client";

import React, { useEffect, useRef, useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faChevronDown, faMoon, faSun } from '@fortawesome/free-solid-svg-icons';
import { useTheme, type Theme } from '../context/ThemeContext';

interface ThemeOption {
    value: Theme;
    label: string;
}

const THEME_OPTIONS: ThemeOption[] = [
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
];

const ThemeToggle: React.FC = () => {
    const { theme, setTheme } = useTheme();
    const [open, setOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

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

    const sunIconColor = theme === 'light' ? '#f59e0b' : 'rgba(107, 114, 128, 0.6)';
    const moonIconColor = theme === 'dark' ? '#a5b4fc' : 'rgba(107, 114, 128, 0.6)';
    const currentIcon = theme === 'light' ? faSun : faMoon;
    const currentIconColor = theme === 'light' ? sunIconColor : moonIconColor;
    const currentLabel = theme === 'light' ? 'Light' : 'Dark';

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
                aria-label={`Theme: ${currentLabel}`}
                aria-expanded={open}
                aria-haspopup="listbox"
            >
                <FontAwesomeIcon icon={currentIcon} style={{ fontSize: '13px', color: currentIconColor }} aria-hidden="true" />
                <span style={{ fontSize: '13px' }}>{currentLabel}</span>
                <FontAwesomeIcon icon={faChevronDown} style={{ fontSize: '10px', marginLeft: '4px', opacity: 0.35 }} aria-hidden="true" />
            </button>

            {open && (
                <div
                    role="listbox"
                    aria-label="Select theme"
                    className="absolute right-0 z-50 mt-1 rounded-lg shadow-lg"
                    style={{
                        width: '120px',
                        top: 'calc(100% + 4px)',
                        border: '1px solid var(--border)',
                        backgroundColor: 'var(--bg-surface)',
                    }}
                >
                    {THEME_OPTIONS.map((option) => {
                        const isActive = theme === option.value;
                        const optionIcon = option.value === 'light' ? faSun : faMoon;
                        const optionIconColor = option.value === 'light' ? sunIconColor : moonIconColor;

                        return (
                            <button
                                key={option.value}
                                role="option"
                                aria-selected={isActive}
                                type="button"
                                onClick={() => {
                                    setTheme(option.value);
                                    setOpen(false);
                                }}
                                className="flex w-full items-center justify-between rounded-md px-2 transition-colors"
                                style={{
                                    height: '32px',
                                    paddingLeft: '8px',
                                    paddingRight: '8px',
                                    color: isActive ? 'var(--text-primary)' : 'rgba(107, 114, 128, 0.6)',
                                    cursor: 'pointer',
                                    backgroundColor: 'transparent',
                                    border: 'none',
                                }}
                                onMouseEnter={(e) => {
                                    (e.currentTarget as HTMLButtonElement).style.backgroundColor = 'var(--bg-hover)';
                                }}
                                onMouseLeave={(e) => {
                                    (e.currentTarget as HTMLButtonElement).style.backgroundColor = 'transparent';
                                }}
                            >
                                <span className="inline-flex items-center gap-2" style={{ fontSize: '13px' }}>
                                    <FontAwesomeIcon icon={optionIcon} style={{ fontSize: '13px', color: optionIconColor }} aria-hidden="true" />
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

export default ThemeToggle;
