import React from 'react';

interface BadgeProps {
  children: React.ReactNode;
  variant?: 'default' | 'secondary' | 'outline' | 'destructive';
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({ children, variant = 'default', className = '' }) => {
  const baseClasses = 'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium';
  const variantClasses = {
    default: 'bg-blue-100 text-blue-800',
    secondary: 'bg-slate-100 text-slate-800',
    outline: 'border border-slate-200 text-slate-700',
    destructive: 'bg-red-100 text-red-800'
  };
  return <span className={`${baseClasses} ${variantClasses[variant]} ${className}`}>{children}</span>;
};