# Use a Node.js release that satisfies the current Next.js runtime floor.
FROM node:20.19.1

# Create and change to the app directory.
WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends ripgrep \
	&& rm -rf /var/lib/apt/lists/*

# Copy package.json and package-lock.json first to leverage Docker cache.
COPY package*.json ./

# Install dependencies.
RUN npm ci

# Copy the rest of the application code.
COPY . .

# Build the application.
RUN npm run build

# Expose the port the app runs on.
EXPOSE 3001

# Start the application.
CMD ["npm", "start"]
