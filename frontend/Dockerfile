FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY public ./public
COPY index.html ./index.html
COPY src ./src

EXPOSE 3000
CMD ["npm", "run", "dev"]
