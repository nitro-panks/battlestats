import React, { useEffect, useRef, useState } from 'react';
import { dispatchPlayerRouteSectionRendered } from './usePlayerRouteDiagnostics';

interface DeferredSectionProps {
    children: React.ReactNode;
    className?: string;
    minHeight?: number;
    placeholder?: React.ReactNode;
    rootMargin?: string;
    sectionId?: string;
    playerId?: number;
}

const DeferredSection: React.FC<DeferredSectionProps> = ({
    children,
    className,
    minHeight = 240,
    placeholder,
    rootMargin = '240px 0px',
    sectionId,
    playerId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const renderAnnouncedRef = useRef(false);
    const [shouldRender, setShouldRender] = useState(false);

    useEffect(() => {
        renderAnnouncedRef.current = false;
    }, [playerId, sectionId]);

    useEffect(() => {
        if (shouldRender) {
            return;
        }

        const node = containerRef.current;
        if (!node || typeof IntersectionObserver === 'undefined') {
            setShouldRender(true);
            return;
        }

        const observer = new IntersectionObserver(
            (entries) => {
                if (entries.some((entry) => entry.isIntersecting)) {
                    setShouldRender(true);
                    observer.disconnect();
                }
            },
            { rootMargin },
        );

        observer.observe(node);
        return () => observer.disconnect();
    }, [rootMargin, shouldRender]);

    useEffect(() => {
        if (!shouldRender || !sectionId || !playerId || renderAnnouncedRef.current) {
            return;
        }

        renderAnnouncedRef.current = true;
        dispatchPlayerRouteSectionRendered(sectionId, playerId, 'deferred');
    }, [playerId, sectionId, shouldRender]);

    return (
        <div ref={containerRef} className={className} data-perf-section={sectionId}>
            {shouldRender ? children : placeholder ?? <div className="animate-pulse rounded-md border border-[#dbe9f6] bg-[#f7fbff]" style={{ minHeight }} />}
        </div>
    );
};

export default DeferredSection;