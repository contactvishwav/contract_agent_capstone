import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useAuth } from './AuthContext';
import { listContracts } from '../services/contractApi';

export interface ContractRecord {
  contract_id: string;
  filename: string;
  upload_date: string;
  model_used: string;
  analysis_completed: boolean;
  risk_score?: number;
  risk_level?: string;
  analysis_results?: unknown;
}

interface ContractHistoryContextType {
  contracts: ContractRecord[];
  addContract: (contract: ContractRecord) => void;
  updateContract: (contract_id: string, updates: Partial<ContractRecord>) => void;
  removeContract: (contract_id: string) => void;
  getContract: (contract_id: string) => ContractRecord | undefined;
  clearHistory: () => void;
  selectedContractId: string | null;
  setSelectedContract: (contract_id: string | null) => void;
}

const ContractHistoryContext = createContext<ContractHistoryContextType | undefined>(undefined);

const isContractRecord = (value: unknown): value is ContractRecord => {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<ContractRecord>;
  return Boolean(
    record.contract_id &&
    record.filename &&
    record.upload_date &&
    record.model_used !== undefined &&
    record.analysis_completed !== undefined
  );
};

export const ContractHistoryProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { session } = useAuth();
  const tenantId = session?.tenantId ?? null;
  const storageKey = useMemo(
    () => (tenantId ? `contract_history:${tenantId}` : null),
    [tenantId]
  );
  const selectionStorageKey = useMemo(
    () => (tenantId ? `contract_selection:${tenantId}` : null),
    [tenantId]
  );
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [selectedContractId, setSelectedContractId] = useState<string | null>(null);
  const [storageReadyKey, setStorageReadyKey] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setContracts([]);
    setSelectedContractId(null);
    setStorageReadyKey(null);
    if (!storageKey) return () => { cancelled = true; };

    let cached: ContractRecord[] = [];
    try {
      const saved = localStorage.getItem(storageKey);
      const parsed = saved ? JSON.parse(saved) : [];
      cached = Array.isArray(parsed) ? parsed.filter(isContractRecord) : [];
    } catch {
      localStorage.removeItem(storageKey);
    }
    setContracts(cached);
    const cachedSelection = selectionStorageKey ? localStorage.getItem(selectionStorageKey) : null;
    setSelectedContractId(cachedSelection);
    setStorageReadyKey(storageKey);

    listContracts()
      .then((serverContracts) => {
        if (cancelled) return;
        setContracts((current) => {
          const merged = serverContracts.map((server) => {
            const local = current.find((item) => item.contract_id === server.contract_id);
            return {
              contract_id: server.contract_id,
              filename: server.filename,
              upload_date: server.upload_date || new Date(0).toISOString(),
              model_used: server.model_used,
              analysis_completed: server.analysis_completed,
              risk_score: server.risk_score ?? undefined,
              risk_level: server.risk_level ?? undefined,
              analysis_results: local?.analysis_results,
            };
          });
          setSelectedContractId((selected) => {
            if (selected && merged.some((item) => item.contract_id === selected)) return selected;
            return merged[0]?.contract_id ?? null;
          });
          return merged;
        });
      })
      .catch(() => {
        // The authenticated server list is authoritative when available;
        // an outage leaves the tenant-scoped cache usable, never another
        // tenant's global browser history.
      });

    return () => { cancelled = true; };
  }, [selectionStorageKey, storageKey]);

  useEffect(() => {
    if (!storageKey || storageReadyKey !== storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(contracts));
    } catch {
      if (contracts.length > 10) setContracts(contracts.slice(0, 10));
    }
  }, [contracts, storageKey, storageReadyKey]);

  useEffect(() => {
    if (!selectionStorageKey) return;
    if (selectedContractId) localStorage.setItem(selectionStorageKey, selectedContractId);
    else localStorage.removeItem(selectionStorageKey);
  }, [selectedContractId, selectionStorageKey]);

  const addContract = useCallback((contract: ContractRecord) => {
    setContracts((current) => [contract, ...current.filter((item) => item.contract_id !== contract.contract_id)]);
    setSelectedContractId(contract.contract_id);
  }, []);

  const updateContract = useCallback((contractId: string, updates: Partial<ContractRecord>) => {
    setContracts((current) => current.map((contract) =>
      contract.contract_id === contractId ? { ...contract, ...updates } : contract
    ));
  }, []);

  const getContract = useCallback(
    (contractId: string) => contracts.find((contract) => contract.contract_id === contractId),
    [contracts]
  );

  const removeContract = useCallback((contractId: string) => {
    setContracts((current) => {
      const remaining = current.filter((contract) => contract.contract_id !== contractId);
      setSelectedContractId((selected) => selected === contractId ? (remaining[0]?.contract_id ?? null) : selected);
      return remaining;
    });
  }, []);

  const clearHistory = useCallback(() => {
    setContracts([]);
    setSelectedContractId(null);
    if (storageKey) localStorage.removeItem(storageKey);
    if (selectionStorageKey) localStorage.removeItem(selectionStorageKey);
  }, [selectionStorageKey, storageKey]);

  return (
    <ContractHistoryContext.Provider value={{
      contracts,
      addContract,
      updateContract,
      removeContract,
      getContract,
      clearHistory,
      selectedContractId,
      setSelectedContract: setSelectedContractId,
    }}>
      {children}
    </ContractHistoryContext.Provider>
  );
};

export const useContractHistory = () => {
  const context = useContext(ContractHistoryContext);
  if (!context) throw new Error('useContractHistory must be used within ContractHistoryProvider');
  return context;
};
