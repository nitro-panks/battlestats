import { useCallback, useEffect, useRef } from 'react';
import type { PlayerData } from './entityTypes';

interface UseClanHydrationPollArgs {
    playerData: PlayerData | null;
    fetchPlayerByName: (playerName: string) => Promise<PlayerData | null>;
    setPlayerData: (data: PlayerData) => void;
    pollLimit: number;
    intervalMs: number;
}

export const useClanHydrationPoll = ({
    playerData,
    fetchPlayerByName,
    setPlayerData,
    pollLimit,
    intervalMs,
}: UseClanHydrationPollArgs) => {
    const attemptsRef = useRef<Record<string, number>>({});

    const resetAttempts = useCallback(() => {
        attemptsRef.current = {};
    }, []);

    useEffect(() => {
        if (!playerData?.name) {
            return;
        }

        const needsHydration = playerData.clan_id && !playerData.clan_name;
        if (!needsHydration) {
            return;
        }

        const playerName = playerData.name;
        const attemptsUsed = attemptsRef.current[playerName] || 0;
        if (attemptsUsed >= pollLimit) {
            return;
        }

        const intervalId = window.setInterval(async () => {
            const currentAttempts = attemptsRef.current[playerName] || 0;
            if (currentAttempts >= pollLimit) {
                window.clearInterval(intervalId);
                return;
            }

            attemptsRef.current[playerName] = currentAttempts + 1;

            try {
                const refreshed = await fetchPlayerByName(playerName);
                if (!refreshed) {
                    return;
                }

                setPlayerData(refreshed);

                if (refreshed.clan_id && refreshed.clan_name) {
                    window.clearInterval(intervalId);
                }
            } catch {
                if ((attemptsRef.current[playerName] || 0) >= pollLimit) {
                    window.clearInterval(intervalId);
                }
            }
        }, intervalMs);

        return () => window.clearInterval(intervalId);
    }, [fetchPlayerByName, intervalMs, playerData, pollLimit, setPlayerData]);

    return { resetAttempts };
};

export default useClanHydrationPoll;