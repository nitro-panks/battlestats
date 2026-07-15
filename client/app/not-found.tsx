export default function NotFound() {
    return (
        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-surface)] px-6 py-10 text-center text-[var(--text-secondary)]">
            <h2 className="text-xl font-semibold text-[var(--accent-mid)]">Page Not Found</h2>
            <p className="mt-2 text-sm">The requested page could not be found.</p>
        </div>
    );
}