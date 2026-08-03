import { createContext, useContext, useEffect } from "react"

type Theme = "dark" | "light" | "system"

type ThemeProviderProps = {
    children: React.ReactNode
    defaultTheme?: Theme
    storageKey?: string
}

type ThemeProviderState = {
    theme: Theme
    setTheme: (theme: Theme) => void
}

const initialState: ThemeProviderState = {
    theme: "light",
    setTheme: () => null,
}

const ThemeProviderContext = createContext<ThemeProviderState>(initialState)

export function ThemeProvider({
    children,
    defaultTheme = "light",
    storageKey = "vite-ui-theme",
    ...props
}: ThemeProviderProps) {
    // Force light theme for legal professionals
    useEffect(() => {
        const root = window.document.documentElement

        // Always use light theme
        root.classList.remove("light", "dark")
        root.classList.add("light")
        
        // Force light theme in body
        document.body.style.backgroundColor = "rgb(248 250 252)"
        document.body.style.color = "rgb(15 23 42)"
    }, [])

    const value = {
        theme: "light" as Theme,
        setTheme: () => {}, // Disable theme switching
    }

    return (
        <ThemeProviderContext.Provider {...props} value={value} >
            {children}
        </ThemeProviderContext.Provider>
    )
}

export const useTheme = () => {
    const context = useContext(ThemeProviderContext)

    if (context === undefined)
        throw new Error("useTheme must be used within a ThemeProvider")

    return context
}