// Theme Toggle Script
(function () {
    const THEME_KEY = 'score_tracker_theme';

    function getPreferredTheme() {
        const stored = localStorage.getItem(THEME_KEY);
        if (stored) return stored;
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }

    function setTheme(theme) {
        document.documentElement.setAttribute('data-bs-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
        updateThemeIcon(theme);
    }

    function updateThemeIcon(theme) {
        const icons = document.querySelectorAll('.theme-toggle-icon');
        icons.forEach(icon => {
            if (theme === 'light') {
                icon.classList.remove('fa-moon');
                icon.classList.add('fa-sun');
                icon.style.color = '#f59e0b';
            } else {
                icon.classList.remove('fa-sun');
                icon.classList.add('fa-moon');
                icon.style.color = '#60a5fa';
            }
        });
    }

    // Initialize immediately
    const currentTheme = getPreferredTheme();
    setTheme(currentTheme);

    document.addEventListener('DOMContentLoaded', () => {
        updateThemeIcon(getPreferredTheme());

        const toggleBtns = document.querySelectorAll('.theme-toggle-btn');
        toggleBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const activeTheme = document.documentElement.getAttribute('data-bs-theme');
                const nextTheme = activeTheme === 'dark' ? 'light' : 'dark';
                setTheme(nextTheme);
            });
        });
    });
})();
