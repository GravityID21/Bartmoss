-- 4-to-1 multiplexer for 8-bit buses. Purely combinational: y follows the selected input.
library ieee;
use ieee.std_logic_1164.all;

entity mux4 is
    port (
        sel : in  std_logic_vector(1 downto 0);
        a   : in  std_logic_vector(7 downto 0);
        b   : in  std_logic_vector(7 downto 0);
        c   : in  std_logic_vector(7 downto 0);
        d   : in  std_logic_vector(7 downto 0);
        y   : out std_logic_vector(7 downto 0)
    );
end entity;

architecture rtl of mux4 is
begin
    process (sel)
    begin
        case sel is
            when "00"   => y <= a;
            when "01"   => y <= b;
            when "10"   => y <= c;
            when others => y <= d;
        end case;
    end process;
end architecture;
