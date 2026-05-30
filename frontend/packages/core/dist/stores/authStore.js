import { create } from 'zustand';
import { getMe } from '../api/auth';
function writeLocaleCookie(locale) {
    if (typeof document === 'undefined')
        return;
    document.cookie = `NEXT_LOCALE=${locale}; path=/; SameSite=Lax`;
}
function clearLocaleCookie() {
    if (typeof document === 'undefined')
        return;
    document.cookie = 'NEXT_LOCALE=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax';
}
export const useAuthStore = create((set) => ({
    user: null,
    isLoading: false,
    error: null,
    async loadMe(client) {
        set({ isLoading: true, error: null });
        try {
            const user = await getMe(client);
            set({ user });
            if (user?.language) {
                writeLocaleCookie(user.language);
                client.setLocale(user.language);
            }
        }
        catch (err) {
            set({ error: err.message });
        }
        finally {
            set({ isLoading: false });
        }
    },
    reset() {
        clearLocaleCookie();
        set({ user: null, isLoading: false, error: null });
    },
}));
//# sourceMappingURL=authStore.js.map