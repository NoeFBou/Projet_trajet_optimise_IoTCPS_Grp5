from pyds import MassFunction


class WasteBinMonitor:
    def __init__(self):
        self.max_volume = 100
        self.states = {'E1', 'E2', 'E3', 'E4', 'E5'}

        self.profiles = {
            "verre": {"ir_trust": 0.9, "us_trust": 0.8, "weight_trust": 0.9, "density": 0.4},
            "plastique": {"ir_trust": 0.6, "us_trust": 0.9, "weight_trust": 0.30, "density": 0.05},
            "organique": {"ir_trust": 0.7, "us_trust": 0.5, "weight_trust": 0.80, "density": 0.6},
            "tout_type": {"ir_trust": 0.7, "us_trust": 0.7, "weight_trust": 0.60, "density": 0.2}
        }

    def discount_mass(self, mf, trust_factor):

        omega = frozenset(self.states)
        m_new = mf * trust_factor
        m_new[omega] += (1.0 - trust_factor)

        return m_new

    def get_mass_from_weight(self, current_weight, trust_factor, density):
        estimated_fill = current_weight / (density * self.max_volume)

        m = MassFunction()

        if estimated_fill > 0.9:
            m[{'E5'}] = 0.75
            m[{'E4'}] = 0.2
            m[{'E3'}] = 0.05
        elif estimated_fill > 0.8:
            m[{'E5'}] = 0.2
            m[{'E4'}] = 0.65
            m[{'E3'}] = 0.1
            m[{'E2'}] = 0.05
        elif estimated_fill > 0.6:
            m[{'E5'}] = 0.05
            m[{'E4'}] = 0.15
            m[{'E3'}] = 0.65
            m[{'E2'}] = 0.1
            m[{'E1'}] = 0.05
        elif estimated_fill > 0.3:
            m[{'E2'}] = 0.7
            m[{'E1'}] = 0.2
            m[{'E3'}] = 0.1
        else:
            m[{'E1'}] = 0.7
            m[{'E2'}] = 0.3

        m.normalize()
        return self.discount_mass(m, trust_factor)

    def get_mass_from_us(self, us1_dist, us2_dist, trust_factor):
        fill1 = max(0, min(1, (self.max_volume - us1_dist) / self.max_volume))
        fill2 = max(0, min(1, (self.max_volume - us2_dist) / self.max_volume))

        max_fill = max(fill1, fill2)
        diff = abs(fill1 - fill2)

        m = MassFunction()

        internal_trust = 1.0
        if diff > 0.2:
            internal_trust = 0.7

        if max_fill >= 0.80:  # 90-100%
            m[{'E5'}] = 0.8
            m[{'E4'}] = 0.2
        elif max_fill >= 0.70:  # 80-90%
            m[{'E4'}] = 0.7
            m[{'E5'}] = 0.15
            m[{'E3'}] = 0.15
        elif max_fill >= 0.50:  # 60-80%
            m[{'E3'}] = 0.8
            m[{'E2'}] = 0.1
            m[{'E4'}] = 0.1
        elif max_fill >= 0.20:  # 30-60%
            m[{'E2'}] = 0.8
            m[{'E1'}] = 0.1
            m[{'E3'}] = 0.1
        else:  # 0-30%
            m[{'E1'}] = 0.8
            m[{'E2'}] = 0.2

        m.normalize()
        total_trust = trust_factor * internal_trust
        return self.discount_mass(m, total_trust)

    def get_mass_from_ir(self, ir25, ir50, ir75, trust_factor):
        m = MassFunction()

        inconsistency = False
        if ir75 == 1 and ir50 == 0:
            inconsistency = True
        if ir50 == 1 and ir25 == 0:
            inconsistency = True

        current_trust = trust_factor * 0.5 if inconsistency else trust_factor

        if ir75 == 1:
            m[{'E3', 'E4', 'E5'}] = 0.9
        elif ir50 == 1:
            m[{'E2', 'E3'}] = 0.8
            m[{'E4'}] = 0.2
        elif ir25 == 1:
            m[{'E1', 'E2'}] = 0.9
        else:
            m[{'E1'}] = 0.9
            m[{'E2'}] = 0.1

        m.normalize()
        return self.discount_mass(m, current_trust)

    def compute_level(self, inputs):
        w_type = inputs.get('type', 'tout_type')
        if w_type not in self.profiles: w_type = 'tout_type'

        cfg = self.profiles[w_type]

        # 1. Création des masses
        m_weight = self.get_mass_from_weight(inputs['weight'], cfg['weight_trust'], cfg['density'])
        m_us = self.get_mass_from_us(inputs['us1'], inputs['us2'], cfg['us_trust'])
        m_ir = self.get_mass_from_ir(inputs['ir25'], inputs['ir50'], inputs['ir75'], cfg['ir_trust'])

        print(f"\n--- Fusion pour {w_type.upper()} ---")

        m_temp = m_weight.combine_conjunctive(m_us, normalization=False)
        m_temp = m_temp.combine_conjunctive(m_ir, normalization=False)

        conflit = m_temp[frozenset()]

        m_final = m_temp.normalize()

        best_state_val = -1
        best_state_name = None

        pignistic_dist = m_final.pignistic()

        if not pignistic_dist:
            print("ERREUR : Conflit total (100%), impossible de décider.")
            return "Inconnu", m_final

        for state, val in pignistic_dist.items():
            state_name = list(state)[0]
            if val > best_state_val:
                best_state_val = val
                best_state_name = state_name

        print(f"Meilleure hypothèse : {best_state_name} avec proba {best_state_val:.2f}")
        print(f"Conflit global : {conflit:.2f}")

        return best_state_name, m_final


