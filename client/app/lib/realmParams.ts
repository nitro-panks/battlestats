export const withRealm = (url: string, realm: string): string => {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}realm=${realm}`;
};
