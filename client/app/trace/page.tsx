import React from "react";

import TraceDashboard from "../components/TraceDashboard";

const TracePage: React.FC = () => {
    return (
        <div className="w-full bg-white">
            <div className="mx-auto w-full max-w-5xl text-black">
                <TraceDashboard />
            </div>
        </div>
    );
};

export default TracePage;