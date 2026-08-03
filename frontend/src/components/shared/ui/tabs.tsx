import * as React from "react"
// import * as TabsPrimitive from "@radix-ui/react-tabs"
// Simple cn utility
const cn = (...classes: (string | undefined)[]) => classes.filter(Boolean).join(' ');

interface TabsProps {
  defaultValue?: string;
  value?: string;
  onValueChange?: (value: string) => void;
  className?: string;
  children: React.ReactNode;
}

const Tabs: React.FC<TabsProps> = ({ defaultValue, value, onValueChange, className, children }) => {
  const [activeTab, setActiveTab] = React.useState(defaultValue || '');
  const currentValue = value !== undefined ? value : activeTab;
  
  const handleValueChange = (newValue: string) => {
    if (value === undefined) setActiveTab(newValue);
    onValueChange?.(newValue);
  };
  
  return (
    <div className={className} data-value={currentValue}>
      {React.Children.map(children, child =>
        React.isValidElement(child)
          ? React.cloneElement(child as React.ReactElement<any>, { currentValue, onValueChange: handleValueChange })
          : child
      )}
    </div>
  );
};

interface TabsListProps {
  className?: string;
  children: React.ReactNode;
  currentValue?: string;
  onValueChange?: (value: string) => void;
}

const TabsList: React.FC<TabsListProps> = ({ className, children, currentValue, onValueChange }) => (
  <div className={cn("inline-flex h-10 items-center justify-center rounded-md bg-slate-100 p-1 text-slate-500", className)}>
    {React.Children.map(children, child =>
      React.isValidElement(child)
        ? React.cloneElement(child as React.ReactElement<any>, { currentValue, onValueChange })
        : child
    )}
  </div>
);

interface TabsTriggerProps {
  value: string;
  className?: string;
  children: React.ReactNode;
  currentValue?: string;
  onValueChange?: (value: string) => void;
}

const TabsTrigger: React.FC<TabsTriggerProps> = ({ value: triggerValue, className, children, currentValue, onValueChange }) => {
  const isActive = currentValue === triggerValue;
  
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium transition-all duration-200",
        isActive ? "bg-blue-600 text-white shadow-lg ring-2 ring-blue-300" : "text-slate-600 hover:text-slate-900 hover:bg-slate-200",
        className
      )}
      onClick={() => onValueChange?.(triggerValue)}
    >
      {children}
    </button>
  );
};

interface TabsContentProps {
  value: string;
  className?: string;
  children: React.ReactNode;
  currentValue?: string;
}

const TabsContent: React.FC<TabsContentProps> = ({ value: contentValue, className, children, currentValue }) => {
  if (currentValue !== contentValue) return null;
  
  return (
    <div className={cn("mt-2", className)}>
      {children}
    </div>
  );
};

export { Tabs, TabsList, TabsTrigger, TabsContent }