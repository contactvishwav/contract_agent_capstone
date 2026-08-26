import React from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/shared/ui/tabs';
import { TechStackTab } from '../components/features/documentation/TechStackTab';
import { ProductionTab } from '../components/features/documentation/ProductionTab';
import { AgentsPage } from './AgentsPage';
import { WorkflowsPage } from './WorkflowsPage';
import { USPPage } from './USPPage';

export const DocumentationPage: React.FC = () => {
  return (
    <div className="space-y-8">
      <div className="text-center bg-white rounded-lg p-8 shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold text-slate-800 mb-3">AI Agent Architecture</h1>
        <p className="text-lg text-slate-600 max-w-3xl mx-auto">
          Explore our multi-agent system that powers intelligent contract analysis through 
          specialized AI agents working together in coordinated workflows.
        </p>
      </div>

      <Tabs defaultValue="usp" className="w-full">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="usp">Business Benefits</TabsTrigger>
          <TabsTrigger value="agents">AI Agents</TabsTrigger>
          <TabsTrigger value="workflows">Workflows</TabsTrigger>
          <TabsTrigger value="architecture">Tech Stack</TabsTrigger>
          <TabsTrigger value="production">Prototype to Production</TabsTrigger>
        </TabsList>

        <TabsContent value="usp" className="space-y-6">
          <USPPage />
        </TabsContent>

        <TabsContent value="agents" className="space-y-6">
          <AgentsPage />
        </TabsContent>

        <TabsContent value="workflows" className="space-y-6">
          <WorkflowsPage />
        </TabsContent>

        <TabsContent value="architecture" className="space-y-6">
          <TechStackTab />
        </TabsContent>

        <TabsContent value="production" className="space-y-6">
          <ProductionTab />
        </TabsContent>
      </Tabs>
    </div>
  );
};